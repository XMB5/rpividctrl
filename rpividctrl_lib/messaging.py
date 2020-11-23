import struct
from enum import IntEnum, IntFlag
import socket
from gi.repository import GLib


REMOTE_CONTROL_PORT = 1875
RTP_PORT = 1874


# To list all options for the camera, execute `gst-inspect-1.0 rpicamsrc` on the raspberry pi
# For the h264 encoder, see `gst-inspect-1.0 omxh264enc` on the rpi

class MessageType(IntEnum):
    SET_RESOLUTION_FRAMERATE = 0  # resolution and framerate are set together because changing either requires creating a new CapsFilter
    PAUSE = 1
    RESUME = 2
    STATS_REQUEST = 3
    STATS_RESPONSE = 4
    SET_ANNOTATION_MODE = 5
    SET_DRC_LEVEL = 6
    SET_TARGET_BITRATE = 7


class AnnotationMode(IntFlag):
    """
    GstRpiCamSrcAnnotationMode

    raspberry pi camera debug overlays

    These are flags, so multiple can be applied at once (i.e. BLACK_BACKGROUND | FRAME_NUMBER)
    """
    NONE = 0x00000000
    CUSTOM_TEXT = 0x00000001
    TEXT = 0x00000002
    DATE = 0x00000004
    TIME = 0x00000008
    SHUTTER_SETTINGS = 0x00000010
    CAF_SETTINGS = 0x00000020  # caf == continuous auto focus?
    GAIN_SETTINGS = 0x00000040
    LENS_SETTINGS = 0x00000080
    MOTIONS_SETTINGS = 0x00000100
    FRAME_NUMBER = 0x00000200
    BLACK_BACKGROUND = 0x00000400


class DRCLevel(IntEnum):
    """GstRpiCamSrcDRCLevel

    dynamic range compression

    When turned on, this settings makes dark areas of the image brighter
    """
    OFF = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class MessageReader:
    """
    Organizes incoming bytes into messages.

    Each messages starts with a 2-byte length, then a 1-byte message type, and then 0 or more bytes
    specific to that type of message. The length includes the 1-byte message type.
    """
    MAX_BYTES_AVAILABLE = 50000

    def __init__(self):
        self.bufs = []
        self.bytes_available = 0
        self.next_message_len = -1

    def append(self, buf):
        self.bufs.append(buf)
        self.bytes_available += len(buf)
        if self.bytes_available > MessageReader.MAX_BYTES_AVAILABLE:
            raise IOError('bytes stored exceeds maximum')

    def read_chunk(self, num_bytes):
        if self.bytes_available >= num_bytes:
            chunk_bufs = []
            bytes_needed = num_bytes
            while bytes_needed > 0:
                next_buf = self.bufs[0]
                next_buf_len = len(next_buf)
                if next_buf_len > bytes_needed:
                    next_buf_lower = next_buf[0:bytes_needed]
                    next_buf_upper = next_buf[bytes_needed:]

                    chunk_bufs.append(next_buf_lower)
                    bytes_needed = 0

                    self.bufs[0] = next_buf_upper
                else:
                    chunk_bufs.append(next_buf)
                    bytes_needed -= next_buf_len
                    self.bufs.pop(0)
            self.bytes_available -= num_bytes
            return b''.join(chunk_bufs)
        else:
            return None

    def read_message(self):
        if self.next_message_len == -1:
            # read big endian uint16_t for next message length
            next_message_len_bytes = self.read_chunk(2)
            if next_message_len_bytes:
                self.next_message_len = struct.unpack('>H', next_message_len_bytes)[0]
            else:
                return None

        message = self.read_chunk(self.next_message_len)
        if message:
            self.next_message_len = -1
            return self.parse_message(message)
        else:
            return None

    def parse_message(self, message):
        message_type = message[0]

        info = {
            'message_type': MessageType(message_type)
        }

        content = message[1:]

        if message_type == MessageType.SET_RESOLUTION_FRAMERATE:
            info['width'], info['height'], info['framerate'] = struct.unpack('>3H', content)
        elif message_type == MessageType.SET_ANNOTATION_MODE:
            info['annotation_mode'] = AnnotationMode(struct.unpack('>H', content)[0])
        elif message_type == MessageType.SET_DRC_LEVEL:
            info['drc_level'] = DRCLevel(struct.unpack('B', content)[0])
        elif message_type == MessageType.SET_TARGET_BITRATE:
            info['target_bitrate'] = struct.unpack('>I', content)[0]
        elif message_type == MessageType.STATS_RESPONSE:
            info['stats_tuple'] = struct.unpack('3f', content)

        return info


class MessageBuilder:

    # these 4 declared here for pycharm autocomplete
    RESUME = None
    PAUSE = None
    STATS_REQUEST = None

    @staticmethod
    def len_to_bytes(message_len):
        return struct.pack('>H', message_len)

    @staticmethod
    def single_byte_command(message_type: MessageType):
        return MessageBuilder.MESSAGE_LEN_1 + bytes([message_type])

    @staticmethod
    def set_resolution_framerate(width, height, framerate):
        return MessageBuilder.SET_RESOLUTION_FRAMERATE_HEADER + struct.pack('>3H', width, height, framerate)

    @staticmethod
    def set_annotation_mode(annotation_mode):
        return MessageBuilder.SET_ANNOTATION_MODE_HEADER + struct.pack('>H', int(annotation_mode))

    @staticmethod
    def set_drc_level(drc_level):
        return MessageBuilder.SET_DRC_LEVEL_HEADER + struct.pack('B', int(drc_level))

    @staticmethod
    def set_target_bitrate(bps):
        return MessageBuilder.SET_TARGET_BITRATE_HEADER + struct.pack('>I', bps)

    @staticmethod
    def stats_response(stats_tuple):
        return MessageBuilder.STATS_RESPONSE_HEADER + struct.pack('3f', *stats_tuple)


MessageBuilder.MESSAGE_LEN_1 = MessageBuilder.len_to_bytes(1)
MessageBuilder.SET_RESOLUTION_FRAMERATE_HEADER = MessageBuilder.len_to_bytes(7) + bytes([MessageType.SET_RESOLUTION_FRAMERATE])
MessageBuilder.SET_ANNOTATION_MODE_HEADER = MessageBuilder.len_to_bytes(3) + bytes([MessageType.SET_ANNOTATION_MODE])
MessageBuilder.SET_DRC_LEVEL_HEADER = MessageBuilder.len_to_bytes(2) + bytes([MessageType.SET_DRC_LEVEL])
MessageBuilder.SET_TARGET_BITRATE_HEADER = MessageBuilder.len_to_bytes(5) + bytes([MessageType.SET_TARGET_BITRATE])
MessageBuilder.STATS_RESPONSE_HEADER = MessageBuilder.len_to_bytes(13) + bytes([MessageType.STATS_RESPONSE])
MessageBuilder.PAUSE = MessageBuilder.single_byte_command(MessageType.PAUSE)
MessageBuilder.RESUME = MessageBuilder.single_byte_command(MessageType.RESUME)
MessageBuilder.STATS_REQUEST = MessageBuilder.single_byte_command(MessageType.STATS_REQUEST)


class SocketManager:
    """
    Manages an IPv4 TCP socket by listening for events on the GLib main loop

    note: sets TCP_NODELAY
    """

    def __init__(self, sock: socket.socket = None):
        if sock:
            self.sock = sock
        else:
            self.sock = socket.socket()
        self.sock.setblocking(False)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.in_listener_id = GLib.io_add_watch(self.sock, GLib.IO_IN, self.in_listener)
        self.out_listener_id = GLib.io_add_watch(self.sock, GLib.IO_OUT, self.out_listener)
        self.connect_timeout_id = None
        self.on_destroy = None
        self.on_connected = None
        self.on_read_message = None
        self.message_reader = MessageReader()
        self.cork_buffer = None

    def connect(self, host, port, timeout=10000):
        try:
            self.sock.connect((host, port))
            raise IOError('expected BlockingIOError')
        except BlockingIOError:
            # expected
            self.connect_timeout_id = GLib.timeout_add(timeout, self.connect_timeout_handler, None)
            pass
        except IOError as e:
            self.destroy(str(e))

    def connect_timeout_handler(self, userdata):
        self.destroy('connect timeout')
        self.connect_timeout_id = None
        return GLib.SOURCE_REMOVE

    def in_listener(self, sock, *args):
        try:
            read_bytes = sock.recv(4096)
            if not len(read_bytes):
                self.in_listener_id = None  # will remove by returning SOURCE_REMOVE
                self.destroy('connection closed')
                return GLib.SOURCE_REMOVE
            else:
                self.recv_bytes_handler(read_bytes)
                return GLib.SOURCE_CONTINUE
        except IOError as e:
            self.in_listener_id = None
            self.destroy(str(e))
            return GLib.SOURCE_REMOVE

    def recv_bytes_handler(self, read_bytes):
        self.message_reader.append(read_bytes)
        while True:
            message = self.message_reader.read_message()
            if message:
                if self.on_read_message:
                    self.on_read_message(message)
            else:
                break

    def out_listener(self, sock, *args):
        if self.on_connected:
            if self.connect_timeout_id is None:
                raise IOError('connected, but connection previously timed out')
            GLib.source_remove(self.connect_timeout_id)
            self.connect_timeout_id = None
            self.on_connected()
        self.out_listener_id = None
        return GLib.SOURCE_REMOVE

    def cork(self):
        """Starts buffering outgoing messages in memory. Uncork with SocketManager.uncork()

        You should cork the socket before sending multiple messages right after one another.
        This way, all the messages will be sent together, in one packet, instead of being split up into multiple packets.
        (TCP_NODELAY is turned on)"""
        self.cork_buffer = []

    def uncork(self):
        """Flushes all the buffered messages"""
        self.sock.sendall(b''.join(self.cork_buffer))
        self.cork_buffer = None

    def sendall(self, bytes_to_send):
        if self.cork_buffer is None:
            self.sock.sendall(bytes_to_send)
        else:
            self.cork_buffer.append(bytes_to_send)

    def getpeername(self):
        """IP address of other computer"""
        return self.sock.getpeername()

    def destroy(self, reason=None):
        if self.sock is None:
            raise IOError('already destroyed')
        for listener_id in [self.in_listener_id, self.out_listener_id, self.connect_timeout_id]:
            if listener_id is not None:
                GLib.source_remove(listener_id)
        self.sock.close()
        self.sock = None
        if self.on_destroy:
            self.on_destroy(reason)
