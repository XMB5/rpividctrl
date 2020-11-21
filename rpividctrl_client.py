import gi
gi.require_version('Gst', '1.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gst, Gtk, GLib
import signal
import logging
from rpividctrl_lib.messaging import REMOTE_CONTROL_PORT, RTP_PORT, MessageBuilder, SocketManager, MessageType, AnnotationMode, DRCLevel
import time
import cairo
import math
import json
from argparse import ArgumentParser

logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s %(name)s] %(message)s')
logger = logging.getLogger('rpividctrl_client')


class VideoWidget(Gtk.Overlay):
    """The GUI element in the middle of the window with the video stream and any overlays"""

    def __init__(self, h264dec_factory=None, **kwargs):
        super().__init__(**kwargs)

        self.rtph264depay = None
        self.rtpjitterbuffer = None
        self.h264dec = None
        self.videoconvert = None
        self.imagesink = None
        self.imagesink_widget = None

        self.h264dec_factory = h264dec_factory

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

        self.rtpjitterbuffer = Gst.ElementFactory.make('rtpjitterbuffer')
        self.rtpjitterbuffer.set_property('latency', 0)
        self.pipeline.add(self.rtpjitterbuffer)
        udpsrc_caps_filter.link(self.rtpjitterbuffer)

        self.rtph264depay = Gst.ElementFactory.make('rtph264depay')
        self.pipeline.add(self.rtph264depay)
        self.rtpjitterbuffer.link(self.rtph264depay)

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

        self.h264dec = self.create_h264_decoder()
        self.pipeline.add(self.h264dec)
        self.rtph264depay.link(self.h264dec)

        self.videoconvert = Gst.ElementFactory.make('videoconvert')
        self.pipeline.add(self.videoconvert)
        self.h264dec.link(self.videoconvert)

        self.imagesink = Gst.ElementFactory.make('gtksink')
        self.imagesink.set_property('sync', False)
        self.pipeline.add(self.imagesink)
        self.videoconvert.link(self.imagesink)

        self.imagesink_widget = self.imagesink.get_property('widget')
        # self.pack_start(self.imagesink_widget, True, True, 0)
        self.add(self.imagesink_widget)
        self.imagesink_widget.show()

        drawing_area = Gtk.DrawingArea()
        drawing_area.connect('draw', self.draw)
        self.add_overlay(drawing_area)
        drawing_area.show()

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

    def create_h264_decoder(self):
        decoder = self.h264dec_factory.create()
        if decoder.get_factory().get_name() == 'vaapih264dec':
            # vaapi hardware-accelerated h264 decoding
            # https://en.wikipedia.org/wiki/Video_Acceleration_API
            try:
                decoder.set_property('low-latency', True)
            except TypeError:
                logger.warning('vaapih264dec property low-latency does not exist, using an old version of libgstvaapi or version compiled without low-latency feature')
        return decoder

    def change_h264_decoder(self, element_factory):
        self.h264dec_factory = element_factory

        self.pipeline.set_state(Gst.State.NULL)

        self.rtph264depay.unlink(self.h264dec)
        self.h264dec.unlink(self.videoconvert)
        self.pipeline.remove(self.h264dec)

        self.h264dec = self.create_h264_decoder()
        self.pipeline.add(self.h264dec)
        self.rtph264depay.link(self.h264dec)
        self.h264dec.link(self.videoconvert)

        self.pipeline.set_state(Gst.State.PLAYING)

    def draw(self, drawing_area, ctx: cairo.Context):
        # draws the overlay

        # gtksink draws the video frame in the center its allocated space,
        # adding black bars to the sides if the aspect ratio does not match up
        # we need to find the dimensions of the video and the dimensions of the available
        # space so that we can calculate where we should draw the overlay so it goes over
        # the video frame

        # first, get last video frame
        last_sample = self.imagesink.get_property('last_sample')
        if last_sample is None:
            # have not received any video yet
            return
        # extract width and height of video stream from last_sample
        caps = last_sample.get_caps()
        vid_orig_width = None
        vid_orig_height = None

        for i in range(caps.get_size()):
            structure = caps.get_structure(i)
            width_tuple = structure.get_int('width')
            if width_tuple[0]:
                vid_orig_width = width_tuple[1]
            height_tuple = structure.get_int('height')
            if height_tuple[0]:
                vid_orig_height = height_tuple[1]
            if vid_orig_width is not None and vid_orig_height is not None:
                break

        if vid_orig_width is None or vid_orig_height is None:
            raise ValueError('found last sample, but could not determine width or height of sample')

        # get size of available space
        allocated_width = drawing_area.get_allocated_width()
        allocated_height = drawing_area.get_allocated_height()

        vid_size_multiplier = min(allocated_width / vid_orig_width, allocated_height / vid_orig_height)
        vid_width = vid_orig_width * vid_size_multiplier
        vid_height = vid_orig_height * vid_size_multiplier

        if allocated_width > vid_width:
            # black bars on left and right
            vid_x = (allocated_width - vid_width) / 2
            vid_y = 0
        else:
            # black bars on top and bottom
            vid_x = 0
            vid_y = (allocated_height - vid_height) / 2

        # now, we choose dimensions we want to work with
        ctx_width = 640
        ctx_height = 480

        # and we transform ctx so that we can use
        # (0, 0) -> top left of video stream, and
        # (ctx_width, ctx_height) -> bottom right of video stream
        ctx.translate(vid_x, vid_y)
        ctx.scale(vid_width / ctx_width, vid_height / ctx_height)

        # now we can use ctx
        # cairo.Context docs: https://pycairo.readthedocs.io/en/latest/reference/context.html

        ctx.set_source_rgb(1, 0, 0)
        ctx.translate(320, 240)
        ctx.arc(0, 0, 20, 0, 2 * math.pi)
        ctx.stroke()


class RemoteControl:
    """Manages the connection to the camera server

    One level higher than SocketManager"""

    STATUS_DISCONNECTED = 0
    STATUS_CONNECTING = 1
    STATUS_CONNECTED = 2

    def __init__(self, on_status_change, on_ping_update):
        self.sock_manager = None
        self.on_status_change = on_status_change
        self.on_ping_update = on_ping_update
        self.status = RemoteControl.STATUS_DISCONNECTED
        self.reason = None
        self.reconnect_timeout_id = None
        self.ping_timer_id = None
        self.ping_time = None

        self.ip_address = None
        self.width = 0
        self.height = 0
        self.framerate = 0
        self.annotation_mode = None
        self.drc_level = None
        self.target_bitrate = 0

    def set_status(self, status, reason=None):
        """used within the class to propogate a status changed event"""
        self.status = status
        self.reason = reason
        self.on_status_change(status, reason)

    def connect(self):
        logger.info(f'connect to {self.ip_address}')
        self.set_status(RemoteControl.STATUS_CONNECTING)
        self.sock_manager = SocketManager()
        self.sock_manager.on_destroy = self.on_sock_destroy
        self.sock_manager.on_connected = self.on_sock_connected
        self.sock_manager.on_read_message = self.on_sock_read_message
        self.sock_manager.connect(self.ip_address, REMOTE_CONTROL_PORT)

    def on_sock_destroy(self, reason=None):
        self.sock_manager = None
        self.reconnect(reason)

    def on_sock_connected(self):
        logger.info('sock connected')
        self.set_status(RemoteControl.STATUS_CONNECTED)
        self.sock_manager.cork()
        self.send_ping()
        self.send_annotation_mode()
        self.send_drc_level()
        self.send_target_bitrate()
        # send resolution/framerate last, because this command resumes the stream
        # this way, we don't need to change any options while the stream is playing,
        # which can cause some delay
        self.send_resolution_framerate()
        self.sock_manager.uncork()
        self.ping_timer_id = GLib.timeout_add(1000, self.send_ping)

    def on_sock_read_message(self, message):
        message_type = message['message_type']
        if message_type == MessageType.PONG:
            if self.ping_time is None:
                logger.warning('received ping without ever sending pong')
            else:
                pong_time = time.monotonic()
                rtt = pong_time - self.ping_time
                self.on_ping_update(rtt)
                self.ping_time = None

    def reconnect(self, disconnect_reason=None, reconnect_delay=1500):
        logger.info(f'disconnect with reason {disconnect_reason}, reconnect in {reconnect_delay} ms')
        self.set_status(RemoteControl.STATUS_DISCONNECTED, disconnect_reason)
        if self.ping_timer_id is not None:
            GLib.source_remove(self.ping_timer_id)
            self.ping_timer_id = None
            self.ping_time = None

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

    def send_ping(self):
        if self.ping_time is None:
            self.ping_time = time.monotonic()
            self.send_if_connected(MessageBuilder.PING)
        else:
            logger.warning('time to send another ping, but have not received last ping\'s response')
        return GLib.SOURCE_CONTINUE

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

    def send_annotation_mode(self):
        self.send_if_connected(MessageBuilder.set_annotation_mode(self.annotation_mode))

    def annotation_mode_changed(self, annotation_mode):
        self.annotation_mode = annotation_mode
        self.send_annotation_mode()

    def send_drc_level(self):
        self.send_if_connected(MessageBuilder.set_drc_level(self.drc_level))

    def drc_level_changed(self, drc_level):
        self.drc_level = drc_level
        self.send_drc_level()

    def send_target_bitrate(self):
        self.send_if_connected(MessageBuilder.set_target_bitrate(self.target_bitrate))

    def target_bitrate_changed(self, bps):
        self.target_bitrate = bps
        self.send_target_bitrate()


class VideoAppWindow(Gtk.ApplicationWindow):
    def __init__(self, settings):
        super().__init__(title='rpividctrl_client')

        ip_address = settings.get('ip_address') or '127.0.0.1'
        width = settings.get('width') or 320
        height = settings.get('height') or 240
        framerate = settings.get('framerate') or 30
        default_h264_decoder = Gst.ElementFactory.find(settings.get('h264_decoder_name') or 'avdec_h264')  # avdec_h264 is software h264 decoder
        annotation_mode_str = settings.get('annotation_mode') or 'none'
        drc_level_str = settings.get('drc_level') or 'off'
        target_birtate_str = settings.get('target_bitrate') or '1M'
        
        self.remote_control = RemoteControl(self.remote_control_status_change, self.remote_control_ping_update)

        self.prev_success_pkts = 0
        self.prev_failure_pkts = 0

        self.grid = Gtk.Grid()
        self.add(self.grid)

        # remote bar -- top of window
        # settings pertaining to the remote host

        remote_bar = Gtk.ActionBar(hexpand=True)

        ip_address_entry = Gtk.Entry()
        ip_address_entry.set_width_chars(15)
        ip_address_entry.set_text(ip_address)
        ip_address_entry.connect('changed', self.on_ip_address_changed)
        remote_bar.add(ip_address_entry)
        self.remote_control.ip_address_changed(ip_address, reconnect=False)

        pause = Gtk.Button.new_from_icon_name('media-playback-pause', Gtk.IconSize.LARGE_TOOLBAR)
        remote_bar.add(pause)
        pause.connect('clicked', self.on_pause_clicked)

        play = Gtk.Button.new_from_icon_name('media-playback-start', Gtk.IconSize.LARGE_TOOLBAR)
        remote_bar.add(play)
        play.connect('clicked', self.on_play_clicked)

        # resolution

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
        remote_bar.add(resolution_combobox)
        self.remote_control.resolution_changed(width, height)

        # framerate

        framerate_store = Gtk.ListStore(int, str)
        framerate_store.append([120, '120fps'])
        framerate_store.append([90, '90fps'])
        framerate_store.append([60, '60fps'])
        framerate_store.append([45, '45fps'])
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
        remote_bar.add(framerate_combobox)
        self.remote_control.framerate_changed(framerate)

        # annotation-mode

        annotation_mode_label = Gtk.Label()
        annotation_mode_label.set_text('annotation mode:')
        remote_bar.add(annotation_mode_label)

        annotation_store = Gtk.ListStore(str, int)
        annotation_store.append(['none', AnnotationMode.NONE])
        annotation_store.append(['text', AnnotationMode.TEXT | AnnotationMode.BLACK_BACKGROUND])
        annotation_store.append(['date', AnnotationMode.DATE | AnnotationMode.BLACK_BACKGROUND])
        annotation_store.append(['time', AnnotationMode.TIME | AnnotationMode.BLACK_BACKGROUND])
        annotation_store.append(['shutter', AnnotationMode.SHUTTER_SETTINGS | AnnotationMode.BLACK_BACKGROUND])
        annotation_store.append(['caf', AnnotationMode.CAF_SETTINGS | AnnotationMode.BLACK_BACKGROUND])
        annotation_store.append(['gain', AnnotationMode.GAIN_SETTINGS | AnnotationMode.BLACK_BACKGROUND])
        annotation_store.append(['lens', AnnotationMode.LENS_SETTINGS | AnnotationMode.BLACK_BACKGROUND])
        annotation_store.append(['motion', AnnotationMode.MOTIONS_SETTINGS | AnnotationMode.BLACK_BACKGROUND])
        annotation_store.append(['frame', AnnotationMode.FRAME_NUMBER | AnnotationMode.BLACK_BACKGROUND])
        annotation_combobox = Gtk.ComboBox.new_with_model(annotation_store)
        for i, annotation_info in enumerate(annotation_store):
            display_str, flags_int = annotation_info
            if display_str == annotation_mode_str:
                annotation_combobox.set_active(i)
                self.remote_control.annotation_mode_changed(AnnotationMode(flags_int))
                set_annotation_active = True
                break
        annotation_combobox.connect('changed', self.on_annotation_mode_changed)
        annotation_renderer = Gtk.CellRendererText()
        annotation_combobox.pack_start(annotation_renderer, True)
        annotation_combobox.add_attribute(annotation_renderer, 'text', 0)
        remote_bar.add(annotation_combobox)

        # drc

        drc_label = Gtk.Label()
        drc_label.set_text('drc:')
        remote_bar.add(drc_label)

        drc_store = Gtk.ListStore(str, int)
        drc_store.append(['off', DRCLevel.OFF])
        drc_store.append(['low', DRCLevel.LOW])
        drc_store.append(['medium', DRCLevel.MEDIUM])
        drc_store.append(['high', DRCLevel.HIGH])
        drc_combobox = Gtk.ComboBox.new_with_model(drc_store)
        for i, drc_info in enumerate(drc_store):
            display_str, flags_int = drc_info
            if display_str == drc_level_str:
                drc_combobox.set_active(i)
                self.remote_control.drc_level_changed(DRCLevel(flags_int))
                set_active_drc = True
                break
        drc_combobox.connect('changed', self.on_drc_level_changed)
        drc_renderer = Gtk.CellRendererText()
        drc_combobox.pack_start(drc_renderer, True)
        drc_combobox.add_attribute(drc_renderer, 'text', 0)
        remote_bar.add(drc_combobox)

        # target-bitrate

        bitrate_label = Gtk.Label()
        bitrate_label.set_text('target bitrate:')
        remote_bar.add(bitrate_label)

        bitrate_store = Gtk.ListStore(str, int)
        bitrate_store.append(['50K', 50000])
        bitrate_store.append(['150K', 150000])
        bitrate_store.append(['500K', 500000])
        bitrate_store.append(['1M', 1000000])
        bitrate_store.append(['2M', 2000000])
        bitrate_combobox = Gtk.ComboBox.new_with_model(bitrate_store)
        for i, bitrate_info in enumerate(bitrate_store):
            display_str, bps = bitrate_info
            if display_str == target_birtate_str:
                bitrate_combobox.set_active(i)
                self.remote_control.target_bitrate_changed(bps)
                break
        bitrate_combobox.connect('changed', self.on_target_bitrate_changed)
        bitrate_renderer = Gtk.CellRendererText()
        bitrate_combobox.pack_start(bitrate_renderer, True)
        bitrate_combobox.add_attribute(bitrate_renderer, 'text', 0)
        remote_bar.add(bitrate_combobox)

        # status labels

        self.connection_status_label = Gtk.Label()
        remote_bar.add(self.connection_status_label)

        self.ping_label = Gtk.Label()
        remote_bar.add(self.ping_label)

        self.grid.attach(remote_bar, 0, 0, 1, 1)

        # video

        self.video = VideoWidget(h264dec_factory=default_h264_decoder, expand=True)
        self.grid.attach_next_to(self.video, remote_bar, Gtk.PositionType.BOTTOM, 1, 1)

        # local bar (controls local video processing)

        local_bar = Gtk.ActionBar(hexpand=True)

        h264_decoder_label = Gtk.Label()
        h264_decoder_label.set_text('h264 decoder:')
        local_bar.add(h264_decoder_label)

        h264_decoders = []
        # find element where only h264 can go in (sinked), and video/x-raw can come out (srced)
        # these are h264 decoders
        h264_caps = Gst.Caps.from_string('video/x-h264')
        for element_factory in Gst.Registry.get().get_feature_list(Gst.ElementFactory):
            h264_sink = False
            raw_src_arr = [False]
            for pad_template in element_factory.get_static_pad_templates():
                if pad_template.direction == Gst.PadDirection.SINK and pad_template.get_caps().is_always_compatible(h264_caps):
                    h264_sink = True
                if pad_template.direction == Gst.PadDirection.SRC:
                    def caps_struct_handle(userdata, struct):
                        if struct.get_name() == 'video/x-raw':
                            # python scope rules...
                            raw_src_arr[0] = True
                    pad_template.get_caps().foreach(caps_struct_handle)
            if h264_sink and raw_src_arr[0]:
                h264_decoders.append(element_factory)
        h264_decoders.sort(key=Gst.ElementFactory.get_name)

        h264_decoders_store = Gtk.ListStore(str, object)
        for h264_decoder in h264_decoders:
            h264_decoders_store.append([h264_decoder.get_name(), h264_decoder])
        h264_decoder_combobox = Gtk.ComboBox.new_with_model(h264_decoders_store)
        for i, h264_decoder_info in enumerate(h264_decoders_store):
            if h264_decoder_info[1] == default_h264_decoder:
                h264_decoder_combobox.set_active(i)
                break
        h264_decoder_renderer = Gtk.CellRendererText()
        h264_decoder_combobox.pack_start(h264_decoder_renderer, True)
        h264_decoder_combobox.add_attribute(h264_decoder_renderer, 'text', 0)
        h264_decoder_combobox.connect('changed', self.on_h264_decoder_changed)
        local_bar.add(h264_decoder_combobox)

        self.grid.attach_next_to(local_bar, self.video, Gtk.PositionType.BOTTOM, 1, 1)

        self.remote_control.connect()

    def remote_control_status_change(self, status, reason):
        # event triggered by the RemoteControl() on connected/disconnected
        if status == RemoteControl.STATUS_DISCONNECTED:
            label_str = 'disconnected'
            if reason:
                label_str += ': ' + reason
            self.connection_status_label.set_label(label_str)
            self.ping_label.set_label('')
        elif status == RemoteControl.STATUS_CONNECTING:
            self.connection_status_label.set_label('connecting')
        elif status == RemoteControl.STATUS_CONNECTED:
            self.connection_status_label.set_label('connected')

    def remote_control_ping_update(self, rtt):
        # event triggered about every second, when ping / round trip time is measured
        rtt_ms = rtt * 1e3

        packet_stats = self.video.rtpjitterbuffer.get_property('stats')
        success_pkts = packet_stats.get_uint64('num-pushed')[1]
        failure_pkts = packet_stats.get_uint64('num-lost')[1] + packet_stats.get_uint64('num-late')[1]
        new_success_pkts = success_pkts - self.prev_success_pkts
        new_failure_pkts = failure_pkts - self.prev_failure_pkts

        self.ping_label.set_label(f'{rtt_ms:.1f} ms  {new_failure_pkts}|{new_success_pkts}')

        self.prev_success_pkts = success_pkts
        self.prev_failure_pkts = failure_pkts

    def on_ip_address_changed(self, entry):
        # when the user types in the ip address textbox
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

    def on_annotation_mode_changed(self, combobox):
        display_str, flags_int = combobox.get_model()[combobox.get_active_iter()]
        annotation_mode = AnnotationMode(flags_int)
        logger.info(f'annotation mode changed to {annotation_mode}')
        self.remote_control.annotation_mode_changed(annotation_mode)
        
    def on_drc_level_changed(self, combobox):
        display_str, flags_int = combobox.get_model()[combobox.get_active_iter()]
        drc_level = DRCLevel(flags_int)
        logger.info(f'drc level changed to {drc_level}')
        self.remote_control.drc_level_changed(drc_level)

    def on_target_bitrate_changed(self, combobox):
        display_str, bps = combobox.get_model()[combobox.get_active_iter()]
        logger.info(f'target bitrate changed to {display_str}')
        self.remote_control.target_bitrate_changed(bps)

    def on_h264_decoder_changed(self, combobox):
        h264_decoder_name, element_factory = combobox.get_model()[combobox.get_active_iter()]
        logger.info(f'h264 decoder changed to {h264_decoder_name}')
        self.video.change_h264_decoder(element_factory)

    def on_play_clicked(self, button):
        logger.info('play clicked')
        self.remote_control.resume()

    def on_pause_clicked(self, button):
        logger.info('pause clicked')
        self.remote_control.pause()


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('-c', '--config', help='path to json config file')
    args = parser.parse_args()

    if args.config is None:
        logger.info('using default settings')
        settings = {}
    else:
        logger.info(f'read settings from {args.config}')
        # see possible settings in VideoAppWindow.__init__()
        with open(args.config) as config_file_handle:
            settings = json.load(config_file_handle)

    logger.info('init gstreamer')
    Gst.init(None)

    logger.info('create app window')
    app = VideoAppWindow(settings)
    app.set_default_size(640, 480)

    app.connect('destroy', Gtk.main_quit)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, Gtk.main_quit)

    app.show_all()

    Gtk.main()
