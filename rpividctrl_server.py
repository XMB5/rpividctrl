import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
import socket
import logging
from rpividctrl_lib.constants import REMOTE_CONTROL_PORT, RTP_PORT
from rpividctrl_lib.messaging import MessageType, SocketManager, MessageBuilder

Gst.init(None)

logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s %(name)s] %(message)s')
logger = logging.getLogger('rpividctrl_server')

class Main:
    def __init__(self, host=''):
        self.mainloop = GLib.MainLoop()

        logger.info('init pipeline')
        self.pipeline = Gst.Pipeline.new()

        self.pipeline.get_bus().add_signal_watch()
        self.pipeline.get_bus().connect('message::eos', self.on_eos)
        self.pipeline.get_bus().connect('message::error', self.on_error)

        self.camsrc = Gst.ElementFactory.make('v4l2src', 'camsrc')
        self.camsrc.set_property('device', '/dev/video0')
        self.pipeline.add(self.camsrc)

        self.camsrc_caps_filter = Gst.ElementFactory.make('capsfilter')
        self.camsrc_caps_filter.set_property('caps',
                                             Gst.Caps.from_string('video/x-raw,width=640,height=480,framerate=15/1'))
        self.pipeline.add(self.camsrc_caps_filter)
        self.camsrc.link(self.camsrc_caps_filter)

        convert = Gst.ElementFactory.make('videoconvert')
        self.pipeline.add(convert)
        self.camsrc_caps_filter.link(convert)

        self.queue0 = Gst.ElementFactory.make('queue')
        self.pipeline.add(self.queue0)
        convert.link(self.queue0)

        self.h264enc = Gst.ElementFactory.make('omxh264enc')
        self.h264enc.set_property('b-frames', 0)
        self.h264enc.set_property('control-rate', 'variable')
        self.h264enc.set_property('target-bitrate', 1000000)
        self.pipeline.add(self.h264enc)
        self.queue0.link(self.h264enc)

        self.queue1 = Gst.ElementFactory.make('queue')
        self.pipeline.add(self.queue1)
        self.h264enc.link(self.queue1)

        self.rtph264pay = Gst.ElementFactory.make('rtph264pay')
        self.pipeline.add(self.rtph264pay)
        self.queue1.link(self.rtph264pay)

        self.udpsink = Gst.ElementFactory.make('udpsink')
        self.udpsink.set_property('port', RTP_PORT)
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
        self.resume()
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
            self.sock_manager.sendall(MessageBuilder().pong().generate())

    def set_dest_host(self, host):
        self.udpsink.set_property('host', host)

    def set_resolution_framerate(self, width, height, framerate):
        logger.info(f'set width {width}, height {height}, framerate {framerate}')
        caps_str = f'video/x-raw,width={width},height={height},framerate={framerate}/1'
        self.camsrc_caps_filter.set_property('caps', Gst.Caps.from_string(caps_str))

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


start = Main()
start.run()
start.resume()