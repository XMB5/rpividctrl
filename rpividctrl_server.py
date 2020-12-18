import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
import socket
import logging
from rpividctrl_lib.messaging import REMOTE_CONTROL_PORT, RTP_PORT, MessageType, SocketManager, MessageBuilder
import time
import collections
from common import get_pad, dict_to_struct, STATS_BUFFER_LEN
import os

logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s %(name)s] %(message)s')
logger = logging.getLogger('rpividctrl_server')

IPV4_UDP_OVERHEAD = 20 + 8  # 20 byte IPv4 header + 8 byte UDP header


class Main:
    def __init__(self, settings):
        host = settings.get('host') or ''  # empty string=listen on all interfaces
        mtu = int(settings.get('mtu') or 1500)

        self.mainloop = GLib.MainLoop()

        logger.info('init pipeline')
        self.pipeline = Gst.Pipeline.new()

        self.pipeline.get_bus().add_signal_watch()
        self.pipeline.get_bus().connect('message::eos', self.on_eos)  # eos==end of stream -- should never happen
        self.pipeline.get_bus().connect('message::error', self.on_error)

        self.camsrc = None
        # we will create camsrc when client connects, so that the camera stays powered off when not used
        # (as soon as we create the camsrc element, the camera is powered on)
        # but this way, when we are not using the camera, another program could start using it and then we wouldn't be able to access it

        self.image_processing = False

        self.width = 640
        self.height = 480
        self.framerate = 60
        self.camsrc_caps_filter = Gst.ElementFactory.make('capsfilter')
        self.camsrc_caps_filter.set_property('caps', self.generate_camsrc_caps())
        self.pipeline.add(self.camsrc_caps_filter)

        # if image_processing is on
        #                                                                  /-> queue -> v4l2convert -> queue -> h264enc -> h264enc_caps_filter -> ...
        # camsrc -> camsrc_caps_filter video/x-raw,format=BGR/other -> tee |
        #                                                                  \-> queue -> appsink
        #
        # if image_processing is off
        # camsrc -> camsrc_caps_filter video/x-h264 -> h264parse -> ...

        self.rtp_queue = Gst.ElementFactory.make('queue', 'rtp_queue')
        self.pipeline.add(self.rtp_queue)

        self.target_bitrate = 1000000

        if self.image_processing:
            self.tee = Gst.ElementFactory.make('tee')
            self.pipeline.add(self.tee)
            self.camsrc_caps_filter.link(self.tee)

            # appsink branch of tee

            self.appsink_queue = Gst.ElementFactory.make('queue', 'appsink_queue')
            self.pipeline.add(self.appsink_queue)
            self.tee.link(self.appsink_queue)

            self.appsink = Gst.ElementFactory.make('appsink')
            self.appsink.set_property('sync', False)
            self.appsink.set_property('emit-signals', True)
            self.appsink.connect('new-sample', self.appsink_new_sample, self.appsink)
            self.pipeline.add(self.appsink)
            self.appsink_queue.link(self.appsink)

            # h264 branch of tee

            self.h264enc_queue = Gst.ElementFactory.make('queue', 'h264enc_queue')
            self.pipeline.add(self.h264enc_queue)
            self.tee.link(self.h264enc_queue)

            self.h264enc = Gst.ElementFactory.make('v4l2h264enc')
            self.h264enc.set_property('extra_controls', dict_to_struct(self.generate_h264enc_controls()))
            self.pipeline.add(self.h264enc)
            self.h264enc_queue.link(self.h264enc)

            self.h264enc_caps_filter = Gst.ElementFactory.make('capsfilter', 'h264enc_caps_filter')
            self.h264enc_caps_filter.set_property('caps', Gst.Caps.from_string('video/x-h264,profile=high'))
            self.pipeline.add(self.h264enc_caps_filter)
            self.h264enc.link(self.h264enc_caps_filter)
            self.h264enc_caps_filter.link(self.rtp_queue)
        else:
            self.h264parse = Gst.ElementFactory.make('h264parse')
            self.pipeline.add(self.h264parse)
            self.camsrc_caps_filter.link(self.h264parse)
            self.h264parse.link(self.rtp_queue)

        # ... -> queue -> rtph264pay -> udpsink

        self.rtph264pay = Gst.ElementFactory.make('rtph264pay')
        self.rtph264pay.set_property('mtu',
                                     mtu - IPV4_UDP_OVERHEAD)  # this property is not the MTU of the link, but rather the maximum udp data size
        self.pipeline.add(self.rtph264pay)
        self.rtp_queue.link(self.rtph264pay)

        self.udpsink = Gst.ElementFactory.make('udpsink')
        self.udpsink.set_property('port', RTP_PORT)
        self.udpsink.set_property('sync', False)
        self.stats_buffer = collections.deque(maxlen=STATS_BUFFER_LEN)
        buffer_processed_pad = get_pad(self.udpsink.iterate_sink_pads())
        buffer_processed_pad.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, self.buffer_processed_probe)
        self.pipeline.add(self.udpsink)
        self.rtph264pay.link(self.udpsink)

        logger.info('init server')
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, REMOTE_CONTROL_PORT))
        sock.listen(5)
        logger.info(f'server listening on {sock.getsockname()}')
        self.sock_manager = None
        GLib.io_add_watch(sock, GLib.IO_IN, self.new_conn_listener)

    def on_eos(self, bus, message):
        logger.error('gstreamer eos')

    def on_error(self, bus, message):
        parsed_error = message.parse_error()
        logger.error(f'gstreamer error: {parsed_error.gerror}\nAdditional debug info:\n{parsed_error.debug}')

    def appsink_new_sample(self, *args):
        sample = self.appsink.emit('pull-sample')
        logger.info(f'appsink sample {sample} {sample.get_caps().to_string()}')
        return Gst.FlowReturn.OK

    def new_conn_listener(self, server_sock, *args):
        # new connection
        conn, addr = server_sock.accept()
        logger.info(f'client connected from {addr}')
        if self.sock_manager is not None:
            logger.info(f'destroy old connection to {self.sock_manager.getpeername()}')
            self.sock_manager.on_destroy = None
            self.sock_manager.destroy()
            self.sock_manager = None
        self.set_dest_host(addr[0])
        self.sock_manager = SocketManager(conn)
        self.sock_manager.on_destroy = self.on_sock_destroy
        self.sock_manager.on_read_message = self.handle_message

        self.pipeline.set_state(Gst.State.NULL)
        self.create_camera_element()

        return True

    def on_sock_destroy(self, reason):
        logger.info(f'sock destroyed, reason {reason}')
        self.sock_manager = None
        self.pause()
        self.destroy_camera_element()

    def handle_message(self, message_info):
        message_type = message_info['message_type']

        if message_type == MessageType.SET_RESOLUTION_FRAMERATE:
            self.set_resolution_framerate(message_info['width'], message_info['height'], message_info['framerate'])
        elif message_type == MessageType.PAUSE:
            self.pause()
        elif message_type == MessageType.RESUME:
            self.resume()
        elif message_type == MessageType.STATS_REQUEST:
            self.sock_manager.sendall(MessageBuilder.stats_response(self.get_average_stats()))
        elif message_type == MessageType.SET_TARGET_BITRATE:
            self.set_target_bitrate(message_info['target_bitrate'])
        else:
            logger.warning(f'do not know how to handle message type {message_type}')

    def set_dest_host(self, host):
        logger.info(f'set dest host {host}')
        self.udpsink.set_property('host', host)

    def camsrc_probe(self, pad, probe_info):
        event_structure = Gst.Structure.new_empty('camsrc_time')
        event_structure.set_value('time', time.monotonic())
        pad.get_peer().send_event(Gst.Event.new_custom(Gst.EventType.CUSTOM_DOWNSTREAM, event_structure))
        return Gst.PadProbeReturn.OK

    def buffer_processed_probe(self, pad, probe_info):
        event = probe_info.get_event()
        if event.type == Gst.EventType.CUSTOM_DOWNSTREAM:
            structure = event.get_structure()
            if structure.has_name('camsrc_time'):
                camsrc_time = event.get_structure().get_value('time')
                now = time.monotonic()
                time_diff = now - camsrc_time
                self.measure_stats(time_diff)
        return Gst.PadProbeReturn.OK

    def get_average_stats(self):
        num_measurements = len(self.stats_buffer)
        if num_measurements > 0:
            latency_sum = 0
            queue0_sum = 0
            queue1_sum = 0
            for latency, queue0_level, queue1_level in self.stats_buffer:
                latency_sum += latency
                queue0_sum += queue0_level
                queue1_sum += queue1_level
            latency_avg = latency_sum / num_measurements
            queue0_avg = queue0_sum / num_measurements
            queue1_avg = queue1_sum / num_measurements
            return latency_avg, queue0_avg, queue1_avg
        else:
            return 0, 0, 0

    def measure_stats(self, last_pipeline_latency):
        # TODO: fix queue stats
        queue0_level = 0 #self.queue0.get_property('current-level-buffers')
        queue1_level = 0 #self.queue1.get_property('current-level-buffers')
        self.stats_buffer.append((last_pipeline_latency, queue0_level, queue1_level))

    def generate_camsrc_controls(self):
        # `v4l2-ctl -L` to list controls
        return {
            'power_line_frequency': 0  # 0==disabled, 1==50hz, 2==60hz, 3==auto, default 50hz
        }

    def generate_h264enc_controls(self):
        # `v4l2-ctl -L` to list controls
        return {
            'video_bitrate': self.target_bitrate,
            'repeat_sequence_header': 1,  # without repeat_sequence_header=True, when client switches decoders, the
                                          # image will freeze until a new h264 encoder element is created
                                          # (for, by example, changing resolution)
            'video_bitrate_mode': 1  # 0==Variable Bitrate, 1==Constant Bitrate
        }

    def generate_camsrc_caps(self):
        if self.image_processing:
            return Gst.Caps.from_string(f'video/x-raw,width={self.width},height={self.height},framerate={self.framerate}/1,format=BGR')
        else:
            return Gst.Caps.from_string(f'video/x-h264,width={self.width},height={self.height},framerate={self.framerate}/1')

    def set_resolution_framerate(self, new_width, new_height, new_framerate):
        """Changes the resolution and framerate"""

        logger.info(f'set resolution {new_width}x{new_height} framerate {new_framerate}')

        self.width = new_width
        self.height = new_height
        self.framerate = new_framerate

        self.pipeline.set_state(Gst.State.PAUSED)
        self.camsrc_caps_filter.set_property('caps', self.generate_camsrc_caps())
        self.pipeline.set_state(Gst.State.PLAYING)

    def set_target_bitrate(self, bitrate):
        logger.info(f'set target bitrate {bitrate}')
        self.target_bitrate = bitrate
        if self.image_processing:
            self.h264enc.set_property('extra_controls', dict_to_struct(self.generate_h264enc_controls()))
        else:
            self.camsrc.set_property('extra_controls', dict_to_struct({**self.generate_h264enc_controls(), **self.generate_camsrc_controls()}))

    def run(self):
        logger.info('run')
        self.pipeline.set_state(Gst.State.PAUSED)
        self.mainloop.run()

    def quit(self):
        logger.info('quit')
        self.mainloop.quit()

    def resume(self):
        logger.info('resume')
        self.pipeline.set_state(Gst.State.PLAYING)

    def pause(self):
        logger.info('pause')
        self.pipeline.set_state(Gst.State.PAUSED)

    def create_camera_element(self):
        logger.info('create camera element')
        self.camsrc = Gst.ElementFactory.make('v4l2src')
        if self.image_processing:
            self.camsrc.set_property('extra_controls', dict_to_struct(self.generate_camsrc_controls()))
        else:
            self.camsrc.set_property('extra_controls', dict_to_struct({**self.generate_camsrc_controls(), **self.generate_h264enc_controls()}))
        src_pad = get_pad(self.camsrc.iterate_src_pads())
        src_pad.add_probe(Gst.PadProbeType.BUFFER, self.camsrc_probe)
        self.pipeline.add(self.camsrc)
        self.camsrc.link(self.camsrc_caps_filter)

    def destroy_camera_element(self):
        logger.info('destory camera element')
        self.pipeline.remove(self.camsrc)
        self.camsrc.set_state(Gst.State.NULL)
        self.camsrc = None
        self.width = None
        self.height = None
        self.framerate = None


if __name__ == '__main__':
    Gst.init(None)
    start = Main({
        'host': os.environ.get('RPIVIDCTRL_SERVER_HOST'),
        'mtu': os.environ.get('RPIVIDCTRL_SERVER_MTU')
    })
    start.run()
