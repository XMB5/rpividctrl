import gi
gi.require_version('Gst', '1.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gst, Gtk, GLib
import signal
import logging
from rpividctrl_lib.constants import REMOTE_CONTROL_PORT, RTP_PORT
from rpividctrl_lib.messaging import MessageBuilder, SocketManager

Gst.init(None)

logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s %(name)s] %(message)s')
logger = logging.getLogger('rpividctrl_client')


class VideoWidget(Gtk.Box):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connect('realize', self.on_realize)
        self.set_size_request(160, 120)

    def on_realize(self, widget):
        self.pipeline = Gst.Pipeline.new()

        udpsrc = Gst.ElementFactory.make('udpsrc')
        udpsrc.set_property('port', RTP_PORT)
        self.pipeline.add(udpsrc)

        udpsrc_caps_filter = Gst.ElementFactory.make('capsfilter')
        udpsrc_caps_filter.set_property('caps', Gst.Caps.from_string('application/x-rtp'))
        self.pipeline.add(udpsrc_caps_filter)
        udpsrc.link(udpsrc_caps_filter)

        rtph264depay = Gst.ElementFactory.make('rtph264depay')
        self.pipeline.add(rtph264depay)
        udpsrc_caps_filter.link(rtph264depay)

        # testsrc = Gst.ElementFactory.make('videotestsrc')
        # self.pipeline.add(testsrc)
        #
        # caps = Gst.ElementFactory.make('capsfilter')
        # caps.set_property('caps', Gst.Caps.from_string('video/x-raw,width=320,height=240,framerate=10/1'))
        # self.pipeline.add(caps)
        # testsrc.link(caps)
        #
        # h264enc = Gst.ElementFactory.make('x264enc')
        # self.pipeline.add(h264enc)
        # caps.link(h264enc)

        avdech264 = Gst.ElementFactory.make('avdec_h264')
        self.pipeline.add(avdech264)
        rtph264depay.link(avdech264)

        videoconvert = Gst.ElementFactory.make('videoconvert')
        self.pipeline.add(videoconvert)
        avdech264.link(videoconvert)

        gtksink = Gst.ElementFactory.make('gtksink')
        gtksink.set_property('sync', True)
        self.pipeline.add(gtksink)
        videoconvert.link(gtksink)

        self.pack_start(gtksink.props.widget, True, True, 0)
        gtksink.props.widget.show()

        self.pipeline.set_state(Gst.State.PLAYING)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message::eos', self.on_eos)
        bus.connect('message::error', self.on_error)

    def on_eos(self, bus, message):
        logger.error('gstreamer eos')

    def on_error(self, bus, message):
        parsed_error = message.parse_error()
        logger.error(f'gstreamer error: {parsed_error.gerror}\nAdditional debug info:\n{parsed_error.debug}')


class RemoteControl:
    # the socket handling is a mess
    STATUS_DISCONNECTED = 0
    STATUS_CONNECTING = 1
    STATUS_CONNECTED = 2

    def __init__(self, on_status_change):
        self.sock_manager = None
        self.on_status_change = on_status_change
        self.status = RemoteControl.STATUS_DISCONNECTED
        self.reason = None
        self.reconnect_timeout_id = None
        self.ip_address = None
        self.width = 0
        self.height = 0
        self.framerate = 0

    def set_status(self, status, reason=None):
        self.status = status
        self.reason = reason
        self.on_status_change(status, reason)

    def connect(self):
        logger.info(f'connect to {self.ip_address}')
        self.set_status(RemoteControl.STATUS_CONNECTING)
        self.sock_manager = SocketManager()
        self.sock_manager.on_destroy = self.on_sock_destroy
        self.sock_manager.on_connected = self.on_sock_connected
        self.sock_manager.connect(self.ip_address, REMOTE_CONTROL_PORT)

    def on_sock_destroy(self, reason=None):
        self.sock_manager = None
        self.reconnect(reason)

    def on_sock_connected(self):
        logger.info('sock connected')
        self.set_status(RemoteControl.STATUS_CONNECTED)
        self.send_resolution_framerate()

    def reconnect(self, disconnect_reason=None, reconnect_delay=1500):
        logger.info(f'disconnect with reason {disconnect_reason}, reconnect in {reconnect_delay} ms')
        self.set_status(RemoteControl.STATUS_DISCONNECTED, disconnect_reason)

        if self.sock_manager:
            self.sock_manager.on_destroy = None
            self.sock_manager.destroy()
            self.sock_manager = None

        if self.reconnect_timeout_id is not None:
            GLib.source_remove(self.reconnect_timeout_id)
            self.reconnect_timeout_id = None

        if reconnect_delay == 0:
            self.connect()
        else:
            self.reconnect_timeout_id = GLib.timeout_add(reconnect_delay, self.reconnect_timeout_handler, None)

    def reconnect_timeout_handler(self, userdata):
        self.reconnect_timeout_id = None
        self.connect()
        return False

    def send_if_connected(self, bytes_to_write):
        if self.status == RemoteControl.STATUS_CONNECTED:
            self.sock_manager.sendall(bytes_to_write)
            return True
        else:
            return False

    def ip_address_changed(self, ip_address, reconnect=True):
        self.ip_address = ip_address
        if reconnect:
            self.reconnect('connect to new ip address', 0)

    def resume(self):
        self.send_if_connected(MessageBuilder.RESUME)

    def pause(self):
        self.send_if_connected(MessageBuilder.PAUSE)

    def send_resolution_framerate(self):
        self.send_if_connected(MessageBuilder.set_resolution_framerate(self.width, self.height, self.framerate))

    def resolution_changed(self, width, height):
        self.width = width
        self.height = height
        self.send_resolution_framerate()

    def framerate_changed(self, framerate):
        self.framerate = framerate
        self.send_resolution_framerate()


class VideoAppWindow(Gtk.ApplicationWindow):
    def __init__(self, settings):
        super().__init__(title='rpividctrl_client')

        ip_address = settings.get('ip_address') or '127.0.0.1'
        width = settings.get('width') or 320
        height = settings.get('height') or 240
        framerate = settings.get('framerate') or 30

        self.grid = Gtk.Grid()
        self.add(self.grid)

        action_bar = Gtk.ActionBar(hexpand=True)

        ip_address_entry = Gtk.Entry()
        ip_address_entry.set_width_chars(15)
        ip_address_entry.set_text(ip_address)
        ip_address_entry.connect('changed', self.on_ip_address_changed)
        action_bar.add(ip_address_entry)

        pause = Gtk.Button.new_from_icon_name('media-playback-pause', Gtk.IconSize.LARGE_TOOLBAR)
        action_bar.add(pause)
        pause.connect('clicked', self.on_pause_clicked)

        play = Gtk.Button.new_from_icon_name('media-playback-start', Gtk.IconSize.LARGE_TOOLBAR)
        action_bar.add(play)
        play.connect('clicked', self.on_play_clicked)

        resolution_store = Gtk.ListStore(int, int, str)
        resolution_store.append([640, 480, '640x480'])
        resolution_store.append([320, 240, '320x240'])
        resolution_store.append([160, 120, '160x120'])
        resolution_combobox = Gtk.ComboBox.new_with_model(resolution_store)
        for i, resolution in enumerate(resolution_store):
            if resolution[0] == width and resolution[1] == height:
                resolution_combobox.set_active(i)
                break
        resolution_combobox.connect('changed', self.on_resolution_changed)
        resolution_renderer = Gtk.CellRendererText()
        resolution_combobox.pack_start(resolution_renderer, True)
        resolution_combobox.add_attribute(resolution_renderer, 'text', 2)
        action_bar.add(resolution_combobox)

        framerate_store = Gtk.ListStore(int, str)
        framerate_store.append([90, '90fps'])
        framerate_store.append([60, '60fps'])
        framerate_store.append([45, '45fps'])
        framerate_store.append([40, '40fps'])
        framerate_store.append([30, '30fps'])
        framerate_store.append([15, '15fps'])
        framerate_combobox = Gtk.ComboBox.new_with_model(framerate_store)
        for i, framerate_info in enumerate(framerate_store):
            if framerate_info[0] == framerate:
                framerate_combobox.set_active(i)
                break
        framerate_combobox.connect('changed', self.on_framerate_changed)
        framerate_renderer = Gtk.CellRendererText()
        framerate_combobox.pack_start(framerate_renderer, True)
        framerate_combobox.add_attribute(framerate_renderer, 'text', 1)
        action_bar.add(framerate_combobox)

        self.status_label = Gtk.Label()
        action_bar.add(self.status_label)

        self.grid.attach(action_bar, 0, 0, 1, 1)

        self.video = VideoWidget(expand=True)
        self.grid.attach_next_to(self.video, action_bar, Gtk.PositionType.BOTTOM, 1, 1)

        self.remote_control = RemoteControl(self.remote_control_status_change)
        self.remote_control.ip_address_changed(ip_address, reconnect=False)
        self.remote_control.resolution_changed(width, height)
        self.remote_control.framerate_changed(framerate)
        self.remote_control.connect()

    def remote_control_status_change(self, status, reason):
        if status == RemoteControl.STATUS_DISCONNECTED:
            label_str = 'disconnected'
            if reason:
                label_str += ': ' + reason
            self.status_label.set_label(label_str)
        elif status == RemoteControl.STATUS_CONNECTING:
            self.status_label.set_label('connecting')
        elif status == RemoteControl.STATUS_CONNECTED:
            self.status_label.set_label('connected')

    def on_ip_address_changed(self, entry):
        ip_address = entry.get_text()
        logger.info(f'ip address changed to {ip_address}')
        self.remote_control.ip_address_changed(ip_address)

    def on_resolution_changed(self, combobox):
        width, height, display_str = combobox.get_model()[combobox.get_active_iter()]
        logger.info(f'resolution changed width {width} height {height}')
        self.remote_control.resolution_changed(width, height)

    def on_framerate_changed(self, combobox):
        framerate, display_str = combobox.get_model()[combobox.get_active_iter()]
        logger.info(f'framerate changed to {framerate}')
        self.remote_control.framerate_changed(framerate)

    def on_play_clicked(self, button):
        logger.info('play clicked')
        self.remote_control.resume()

    def on_pause_clicked(self, button):
        logger.info('pause clicked')
        self.remote_control.pause()


logger.info('create app window')
app = VideoAppWindow({})
app.set_default_size(640, 480)

app.connect('destroy', Gtk.main_quit)
GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, Gtk.main_quit)

app.show_all()

Gtk.main()
