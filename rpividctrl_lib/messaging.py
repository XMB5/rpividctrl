import struct
from enum import IntEnum
import socket
from gi.repository import GLib


class MessageType(IntEnum):
    SET_RESOLUTION_FRAMERATE = 0
    PAUSE = 1
    RESUME = 2
    PING = 3
    PONG = 4


class MessageReader:
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

        return info


class MessageBuilder:

    # declared here for pycharm autocomplete
    RESUME = None
    PAUSE = None
    PING = None
    PONG = None

    @staticmethod
    def len_to_bytes(message_len):
        return struct.pack('>H', message_len)

    @staticmethod
    def single_byte_command(message_type: MessageType):
        return MessageBuilder.MESSAGE_LEN_1 + bytes([message_type])

    @staticmethod
    def set_resolution_framerate(width, height, framerate):
        return MessageBuilder.MESSAGE_LEN_7 + bytes([MessageType.SET_RESOLUTION_FRAMERATE]) + struct.pack('>3H', width, height, framerate)


MessageBuilder.MESSAGE_LEN_1 = MessageBuilder.len_to_bytes(1)
MessageBuilder.MESSAGE_LEN_7 = MessageBuilder.len_to_bytes(7)
MessageBuilder.PAUSE = MessageBuilder.single_byte_command(MessageType.PAUSE)
MessageBuilder.RESUME = MessageBuilder.single_byte_command(MessageType.RESUME)
MessageBuilder.PING = MessageBuilder.single_byte_command(MessageType.PING)
MessageBuilder.PONG = MessageBuilder.single_byte_command(MessageType.PONG)

class SocketManager:
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

    def sendall(self, bytes_to_send):
        self.sock.sendall(bytes_to_send)

    def getpeername(self):
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
