import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
import socket
import logging
from rpividctrl_lib.messaging import REMOTE_CONTROL_PORT, RTP_PORT, MessageType, SocketManager, MessageBuilder, AnnotationMode, DRCLevel

logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s %(name)s] %(message)s')
logger = logging.getLogger('rpividctrl_server')


class Main:
    def __init__(self, host=''):
        self.mainloop = GLib.MainLoop()

        logger.info('init pipeline')
        self.pipeline = Gst.Pipeline.new()

        self.pipeline.get_bus().add_signal_watch()
        self.pipeline.get_bus().connect('message::eos', self.on_eos)  # eos==end of stream -- should never happen
        self.pipeline.get_bus().connect('message::error', self.on_error)

        self.annotation_mode = AnnotationMode.NONE
        self.drc_level = DRCLevel.OFF
        self.camsrc = self.generate_camsrc()
        self.pipeline.add(self.camsrc)

        self.width = 640
        self.height = 480
        self.framerate = 60
        self.camsrc_caps_filter = self.generate_camsrc_capsfilter()
        self.pipeline.add(self.camsrc_caps_filter)
        self.camsrc.link(self.camsrc_caps_filter)

        self.convert = Gst.ElementFactory.make('videoconvert')
        self.pipeline.add(self.convert)
        self.camsrc_caps_filter.link(self.convert)

        self.queue0 = Gst.ElementFactory.make('queue')
        self.pipeline.add(self.queue0)
        self.convert.link(self.queue0)

        self.target_bitrate = 1000000

        self.h264enc = self.generate_h264enc()
        self.pipeline.add(self.h264enc)
        self.queue0.link(self.h264enc)

        self.h264enc_caps_filter = Gst.ElementFactory.make('capsfilter')
        self.h264enc_caps_filter.set_property('caps', Gst.Caps.from_string('video/x-h264,colorimetry=bt709'))
        self.pipeline.add(self.h264enc_caps_filter)
        self.h264enc.link(self.h264enc_caps_filter)

        self.queue1 = Gst.ElementFactory.make('queue')
        self.pipeline.add(self.queue1)
        self.h264enc_caps_filter.link(self.queue1)

        self.rtph264pay = Gst.ElementFactory.make('rtph264pay')
        self.pipeline.add(self.rtph264pay)
        self.queue1.link(self.rtph264pay)

        self.udpsink = Gst.ElementFactory.make('udpsink')
        self.udpsink.set_property('port', RTP_PORT)
        self.udpsink.set_property('sync', False)
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
        # do not need to resume the pipeline here, because the client will send a SET_RESOLUTION_FRAMERATE command which will resume it
        self.sock_manager = SocketManager(conn)
        self.sock_manager.on_destroy = self.on_sock_destroy
        self.sock_manager.on_read_message = self.handle_message
        return True

    def on_sock_destroy(self, reason):
        logger.info(f'sock destroyed, reason {reason}')
        self.sock_manager = None
        self.pause()

    def handle_message(self, message_info):
        logger.info(f'message info {message_info}')

        message_type = message_info['message_type']

        if message_type == MessageType.SET_RESOLUTION_FRAMERATE:
            self.set_resolution_framerate(message_info['width'], message_info['height'], message_info['framerate'])
        elif message_type == MessageType.PAUSE:
            self.pause()
        elif message_type == MessageType.RESUME:
            self.resume()
        elif message_type == MessageType.PING:
            self.sock_manager.sendall(MessageBuilder.PONG)
        elif message_type == MessageType.SET_ANNOTATION_MODE:
            self.set_annotation_mode(message_info['annotation_mode'])
        elif message_type == MessageType.SET_DRC_LEVEL:
            self.set_drc_level(message_info['drc_level'])
        elif message_type == MessageType.SET_TARGET_BITRATE:
            self.set_target_bitrate(message_info['target_bitrate'])
        else:
            logger.warning(f'do not know how to handle message type {message_type}')

    def set_dest_host(self, host):
        self.udpsink.set_property('host', host)

    def generate_camsrc(self):
        new_camsrc = Gst.ElementFactory.make('rpicamsrc')
        new_camsrc.set_property('annotation_mode', self.annotation_mode)
        new_camsrc.set_property('drc', int(self.drc_level))
        return new_camsrc

    def generate_camsrc_capsfilter(self):
        new_camsrc_capsfilter = Gst.ElementFactory.make('capsfilter')
        caps_str = f'video/x-raw,width={self.width},height={self.height},framerate={self.framerate}/1'
        caps = Gst.Caps.from_string(caps_str)
        new_camsrc_capsfilter.set_property('caps', caps)
        return new_camsrc_capsfilter

    def generate_h264enc(self):
        h264enc = Gst.ElementFactory.make('omxh264enc')
        h264enc.set_property('b-frames', 0)
        h264enc.set_property('control-rate', 'variable')  # fails with 'constant'
        h264enc.set_property('target-bitrate', self.target_bitrate)
        return h264enc

    def set_resolution_framerate(self, new_width, new_height, new_framerate):
        """Changes the resolution and framerate, and resumes the pipeline"""

        if new_width == self.width and new_height == self.height and new_framerate == self.framerate:
            logger.info(f'not changing width, height, framerate, because it would be the same')
        else:
            self.width = new_width
            self.height = new_height
            self.framerate = new_framerate

            logger.info(f'set width {self.width}, height {self.height}, framerate {self.framerate}')

            # we need to remove the rpicamsrc element and the following CapsFilter, and then insert new ones into the pipeline
            # I tried keeping the same elements and changing the properties, but it fails with an error like
            # "/GstPipeline:pipeline0/GstRpiCamSrc:src: Waiting for a buffer from the camera took too long"

            self.pipeline.set_state(Gst.State.NULL)
            self.camsrc.unlink(self.camsrc_caps_filter)
            self.camsrc_caps_filter.unlink(self.queue0)
            self.pipeline.remove(self.camsrc)
            self.pipeline.remove(self.camsrc_caps_filter)

            self.camsrc = self.generate_camsrc()
            self.pipeline.add(self.camsrc)

            self.camsrc_caps_filter = self.generate_camsrc_capsfilter()
            self.pipeline.add(self.camsrc_caps_filter)
            self.camsrc.link(self.camsrc_caps_filter)
            self.camsrc_caps_filter.link(self.convert)

        self.pipeline.set_state(Gst.State.PLAYING)

    def set_annotation_mode(self, annotation_mode):
        self.annotation_mode = annotation_mode
        self.camsrc.set_property('annotation_mode', int(self.annotation_mode))

    def set_drc_level(self, drc_level):
        self.drc_level = drc_level
        self.camsrc.set_property('drc', int(self.drc_level))

    def set_target_bitrate(self, bitrate):
        logger.info(f'set target bitrate {bitrate}')
        self.target_bitrate = bitrate
        self.h264enc.set_property('target-bitrate', bitrate)

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


if __name__ == '__main__':
    Gst.init(None)
    start = Main()
    start.run()
    start.resume()
