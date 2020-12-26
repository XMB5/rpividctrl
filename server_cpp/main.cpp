#include <iostream>
#include <gst/gst.h>
#include <glib.h>
#include <glib-unix.h>
#include <csignal>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <string>
#include <stdexcept>

#include "SocketManager.h"
#include "Message.h"

// 20 byte IPv4 header + 8 byte UDP header
#define IPV4_UDP_OVERHEAD (20 + 8)

#define REMOTE_CONTROL_PORT 1875
#define RTP_PORT 1874

class Main {

private:
    GMainLoop *mainLoop;
    GstPipeline *pipeline;
    GstElement *camsrc, *camsrcCapsFilter, *rtpQueue, *tee, *appsinkQueue, *appsink, *h264encQueue, *h264enc,
        *h264encCapsFilter, *h264parse, *rtph264pay, *udpsink;

    bool imageProcessing;
    int width;
    int height;
    int framerate;
    int targetBitrate;

    int serverSockFd;
    GIOChannel *serverSockChannel;
    guint newConnListenerId;
    SocketManager *clientSockManager;

    guint bus_watch_id;

    void setDestHost(const char *host);

    void resume();
    void pause();

    GstCaps *generateCamsrcCaps() const;
    void addCamsrcControls(GstStructure *structure) const;
    void addH264EncControls(GstStructure *structure) const;
    void generateCameraElement();
    void destroyCameraElement();

    void error(const std::string& reason) const;

public:
    Main(const char *host, int mtu);
    ~Main();
    static gboolean busCallWrapper(GstBus *bus, GstMessage *msg, gpointer data);
    gboolean busCall(GstBus *bus, GstMessage *msg);
    static GstFlowReturn newSampleWrapper(GstElement *element, gpointer data);
    GstFlowReturn newSample(GstElement *element);
    static gboolean sigintWrapper(gpointer data);
    gboolean sigint();
    static gboolean newConnWrapper(GIOChannel *source, GIOCondition condition, gpointer data);
    gboolean newConn(GIOChannel *source, GIOCondition condition);
    static void clientSockMessageWrapper(Message *message, void *data);
    void clientSockMessage(Message *message);
    static void clientSockDestroyWrapper(const std::string& reason, void *data);
    void clientSockDestroy(const std::string& reason);
    void run();

};

gboolean Main::busCallWrapper(GstBus *bus, GstMessage *msg, gpointer data) {
    Main *main = (Main*) data;
    return main->busCall(bus, msg);
}

gboolean Main::busCall(GstBus *bus, GstMessage *msg) {
    switch (GST_MESSAGE_TYPE(msg)) {

        case GST_MESSAGE_EOS:
            std::cout << "eos" << std::endl;
            g_main_loop_quit(mainLoop);

            break;

        case GST_MESSAGE_ERROR:
            gchar *debug;
            GError *error;

            gst_message_parse_error(msg, &error, &debug);

            std::cout << "gstreamer error: " << error->message << std::endl \
                      << "Additional debug info:" << std::endl << debug << std::endl;

            g_free(debug);
            g_error_free(error);

            g_main_loop_quit(mainLoop);

            break;

        default:
            break;

    }

    return true;
}

GstFlowReturn Main::newSampleWrapper(GstElement *element, gpointer data) {
    Main *main = (Main*) data;
    return main->newSample(element);
}

GstFlowReturn Main::newSample(GstElement *element) {
    std::cout << "new sample" << std::endl;
    return GST_FLOW_OK;
}

gboolean Main::sigintWrapper(gpointer data) {
    Main *main = (Main*) data;
    return main->sigint();
}

gboolean Main::sigint() {
    std::cout << "caught sigint" << std::endl;
    g_main_loop_quit(this->mainLoop);
    return false;
}

gboolean Main::newConnWrapper(GIOChannel *source, GIOCondition condition, gpointer data) {
    Main *main = (Main*) data;
    return main->newConn(source, condition);
}

gboolean Main::newConn(GIOChannel *source, GIOCondition condition) {
    sockaddr_in clientAddr{};
    socklen_t clientAddrLen = sizeof(clientAddr);
    int clientSockFd = accept(this->serverSockFd, (struct sockaddr*) &clientAddr, &clientAddrLen);
    if (clientSockFd < 0) {
        this->error("accept() failed");
    }

    char remoteIpStr[INET_ADDRSTRLEN];
    if (inet_ntop(AF_INET, &clientAddr.sin_addr, remoteIpStr, INET_ADDRSTRLEN) == nullptr) {
        this->error("inet_ntop() failed");
    }
    std::cout << "new connection from " << remoteIpStr << ':' << ntohs(clientAddr.sin_port) << std::endl;

    if (this->clientSockManager != nullptr) {
        std::cout << "kill previous connection" << std::endl;
        this->clientSockManager->destroy("replaced by new connection"); // destroy handler calls `delete`
    }

    this->setDestHost(remoteIpStr);
    this->clientSockManager = new SocketManager(clientSockFd, clientSockDestroyWrapper, clientSockMessageWrapper, (void*) this);

    gst_element_set_state(GST_ELEMENT(this->pipeline), GST_STATE_NULL);
    this->generateCameraElement();

    return true;
}

void Main::clientSockMessageWrapper(Message *message, void *data) {
    ((Main*) data)->clientSockMessage(message);
}

void Main::clientSockMessage(Message *message) {
    auto *setResFramerateMsg = dynamic_cast<SetResFramerateMessage*>(message);
    if (setResFramerateMsg != nullptr) {
        std::cout << "set res framerate message, width=" << setResFramerateMsg->width << ", height=" << setResFramerateMsg->height << ", framerate=" << setResFramerateMsg->framerate << std::endl;
        return;
    }

    auto *pauseMsg = dynamic_cast<PauseMessage*>(message);
    if (pauseMsg != nullptr) {
        std::cout << "pause message" << std::endl;
        this->pause();
        return;
    }

    auto *resumeMsg = dynamic_cast<ResumeMessage*>(message);
    if (resumeMsg != nullptr) {
        std::cout << "resume message" << std::endl;
        this->resume();
        return;
    }

    auto *statsRequestMessage = dynamic_cast<StatsRequestMessage*>(message);
    if (statsRequestMessage != nullptr) {
        std::cout << "stats req message" << std::endl;
        auto response = StatsResponseMessage(0.0, 0.0, 0.0, 0.0);
        this->clientSockManager->sendMessage(&response);
        return;
    }

    auto *setBitrateMessage = dynamic_cast<SetBitrateMessage*>(message);
    if (setBitrateMessage != nullptr) {
        std::cout << "set bitrate " << setBitrateMessage->bitrate << std::endl;
        return;
    }

    throw std::runtime_error("cannot handle message");
}

void Main::clientSockDestroyWrapper(const std::string &reason, void *data) {
    ((Main*) data)->clientSockDestroy(reason);
}

void Main::clientSockDestroy(const std::string& reason) {
    std::cout << "client sock destroyed, reason " << reason << std::endl;
    delete this->clientSockManager;
    this->clientSockManager = nullptr;

    this->pause();
    this->destroyCameraElement();
}

Main::Main(const char *host, int mtu) {
    this->mainLoop = g_main_loop_new(nullptr, false);

    this->pipeline = GST_PIPELINE(gst_pipeline_new(nullptr));

    GstBus *bus = gst_pipeline_get_bus(pipeline);
    this->bus_watch_id = gst_bus_add_watch(bus, busCallWrapper, this);
    gst_object_unref(bus);

    this->camsrc = nullptr;
    /*
        we will create camsrc when client connects, so that the camera stays powered off when not used
        (as soon as we create the camsrc element, the camera is powered on)
        but this way, when we are not using the camera, another program could start using it and then we wouldn't be able to access it
    */


    this->imageProcessing = false;
    this->width = 640;
    this->height = 480;
    this->framerate = 60;
    this->camsrcCapsFilter = gst_element_factory_make("capsfilter", nullptr);
    GstCaps *camsrcCaps = this->generateCamsrcCaps();
    g_object_set(this->camsrcCapsFilter, "caps", camsrcCaps, nullptr);
    gst_caps_unref(camsrcCaps);
    gst_bin_add(GST_BIN(this->pipeline), this->camsrcCapsFilter);

    this->rtpQueue = gst_element_factory_make("queue", nullptr);
    gst_bin_add(GST_BIN(this->pipeline), this->rtpQueue);

    this->targetBitrate = 1000000;

    /*
        if image_processing is on
                                                                         /-> queue -> v4l2convert -> queue -> h264enc -> h264enc_caps_filter -> ...
        camsrc -> camsrc_caps_filter video/x-raw,format=BGR/other -> tee |
                                                                         \-> queue -> appsink
        if image_processing is off
        camsrc -> camsrc_caps_filter video/x-h264 -> h264parse -> ...
    */

    if (this->imageProcessing) {
        this->tee = gst_element_factory_make("tee", nullptr);
        gst_bin_add(GST_BIN(this->pipeline), this->tee);
        gst_element_link(this->camsrcCapsFilter, this->tee);

        // appsink branch of tee

        this->appsinkQueue = gst_element_factory_make("queue", "appsink_queue");
        gst_bin_add(GST_BIN(this->pipeline), this->appsinkQueue);
        gst_element_link(this->tee, this->appsinkQueue);

        this->appsink = gst_element_factory_make("appsink", nullptr);
        g_object_set(this->appsink, "sync", false,
                     "emit-signals", true, nullptr);
        g_signal_connect(this->appsink, "new-sample", G_CALLBACK(newSampleWrapper), this);
        gst_bin_add(GST_BIN(this->pipeline), this->appsink);
        gst_element_link(this->appsinkQueue, this->appsink);

        // h264 branch of tee

        this->h264encQueue = gst_element_factory_make("queue", "h264enc_queue");
        gst_bin_add(GST_BIN(this->pipeline), this->h264encQueue);
        gst_element_link(this->tee, this->h264encQueue);

        this->h264enc = gst_element_factory_make("v4l2h264enc", nullptr);
        GstStructure *extraControlsStructure = gst_structure_new_empty("extra_controls");
        this->addH264EncControls(extraControlsStructure);
        g_object_set(this->h264enc, "extra_controls", extraControlsStructure, nullptr);
        // extraControlsStructure will be freed when v4l2h264enc is destroyed
        // see https://github.com/GStreamer/gst-plugins-good/blob/27ecd2c30d732d4ccb058157b8d95dc02aebe834/sys/v4l2/gstv4l2object.c#L568
        gst_bin_add(GST_BIN(this->pipeline), this->h264enc);
        gst_element_link(this->h264encQueue, this->h264enc);

        this->h264encCapsFilter = gst_element_factory_make("capsfilter", "h264enc_caps_filter");
        GstCaps *h264encCaps = gst_caps_new_simple("video/x-h264",
                                                   "profile", G_TYPE_STRING, "high",
                                                   nullptr);
        g_object_set(this->h264encCapsFilter, "caps", h264encCaps, nullptr);
        gst_caps_unref(h264encCaps);
        gst_bin_add(GST_BIN(this->pipeline), this->h264encCapsFilter);
        gst_element_link_many(this->h264enc, this->h264encCapsFilter, this->rtpQueue, nullptr);

    } else {

        this->h264parse = gst_element_factory_make("h264parse", nullptr);
        gst_bin_add(GST_BIN(this->pipeline), this->h264parse);
        gst_element_link_many(this->camsrcCapsFilter, this->h264parse, this->rtpQueue, nullptr);

    }

    // ... -> queue -> rtph264pay -> udpsink

    this->rtph264pay = gst_element_factory_make("rtph264pay", nullptr);
    g_object_set(this->rtph264pay, "mtu", mtu - IPV4_UDP_OVERHEAD, nullptr);
    gst_bin_add(GST_BIN(this->pipeline), this->rtph264pay);
    gst_element_link(this->rtpQueue, this->rtph264pay);

    this->udpsink = gst_element_factory_make("udpsink", nullptr);
    g_object_set(this->udpsink, "port", RTP_PORT,
                 "sync", false,
                 nullptr);
    //TODO: buffer_processed_pad stuff
    gst_bin_add(GST_BIN(this->pipeline), this->udpsink);
    gst_element_link(this->rtph264pay, this->udpsink);

    std::cout << "init server" << std::endl;
    this->serverSockFd = socket(AF_INET, SOCK_STREAM, 0);
    if (this->serverSockFd < 0) {
        this->error("failed to create server socket");
    }
    int reuseAddrVal = 1;
    if (setsockopt(this->serverSockFd, SOL_SOCKET, SO_REUSEADDR, &reuseAddrVal, sizeof(reuseAddrVal))) {
        this->error("setsockopt(SO_REUSEADDR) failed");
    }

    struct sockaddr_in addr{};
    addr.sin_family = AF_INET;
    if (strlen(host) == 0) {
        // blank string -> listen on all interfaces
        addr.sin_addr.s_addr = INADDR_ANY;
    } else if (inet_pton(AF_INET, host, &addr.sin_addr) == 0) {
        throw std::runtime_error("invalid ipv4 address provided: " + std::string(host));
    }
    addr.sin_port = htons(REMOTE_CONTROL_PORT);

    if (bind(this->serverSockFd, (struct sockaddr*) &addr, sizeof(addr)) < 0) {
        this->error("bind() failed");
    }

    if (listen(this->serverSockFd, 5) < 0) {
        this->error("listen() failed");
    }

    this->serverSockChannel = g_io_channel_unix_new(this->serverSockFd);
    this->clientSockManager = nullptr;
    this->newConnListenerId = g_io_add_watch(this->serverSockChannel, G_IO_IN, newConnWrapper, this);

}

Main::~Main() {
    if (this->clientSockManager != nullptr) {
        this->clientSockManager->destroy("main destructor"); // destroy handler calls `delete` and pauses pipeline
    }

    gst_element_set_state(GST_ELEMENT(this->pipeline), GST_STATE_NULL);
    gst_object_unref(this->pipeline);

    g_source_remove(this->newConnListenerId); // decreases serverSockChannel refcount
    g_io_channel_unref(this->serverSockChannel); // "GIOChannel instances are created with an initial reference count of 1."

    if (close(this->serverSockFd) < 0) {
        this->error("close() server sock fd failed");
    }

    g_source_remove(this->bus_watch_id);
    g_main_loop_unref(this->mainLoop);
}

void Main::run() {
    std::cout << "run" << std::endl;
    gst_element_set_state(GST_ELEMENT(pipeline), GST_STATE_PAUSED);
    g_unix_signal_add(SIGINT, sigintWrapper, this);
    g_main_loop_run(mainLoop);
}

void Main::resume() {
    std::cout << "resume" << std::endl;
    gst_element_set_state(GST_ELEMENT(this->pipeline), GST_STATE_PLAYING);
}

void Main::pause() {
    std::cout << "pause" << std::endl;
    gst_element_set_state(GST_ELEMENT(this->pipeline), GST_STATE_PAUSED);
}

GstCaps *Main::generateCamsrcCaps() const {
    if (this->imageProcessing) {
        return gst_caps_new_simple("video/x-raw",
                                   "width", G_TYPE_INT, this->width,
                                   "height", G_TYPE_INT, this->height,
                                   "framerate", GST_TYPE_FRACTION, this->framerate, 1,
                                   "format", G_TYPE_STRING, "BGR",
                                   nullptr);
    } else {
        return gst_caps_new_simple("video/x-h264",
                                   "width", G_TYPE_INT, this->width,
                                   "height", G_TYPE_INT, this->height,
                                   "framerate", GST_TYPE_FRACTION, this->framerate, 1,
                                   nullptr);
    }
}

void Main::addCamsrcControls(GstStructure *structure) const {
    gst_structure_set(structure, "power_line_frequency", G_TYPE_INT, 0, // 0==disabled, 1==50hz, 2==60hz, 3==auto, default 50hz
                      nullptr);
}

void Main::addH264EncControls(GstStructure *structure) const {
    gst_structure_set(structure, "video_bitrate", G_TYPE_INT, this->targetBitrate,
                      "repeat_sequence_header", G_TYPE_INT, 1,  // without repeat_sequence_header=True, when client switches decoders, the
                                                                // image will freeze until a new h264 encoder element is created
                                                                // (for, by example, changing resolution)
                      "video_bitrate_mode", G_TYPE_INT, this->imageProcessing ? 0 : 1, // 0==Variable Bitrate, 1==Constant Bitrate
                                                           // constant is preferred because there will be fewer spikes in network traffic,
                                                           // but when imageProcessing==true and using constant bitrate, v4l2h264enc fails with cryptic error:
                                                           // gstv4l2videoenc.c(803): gst_v4l2_video_enc_handle_frame (): /GstPipeline:pipeline0/v4l2h264enc:v4l2h264enc0:
                                                           // Maybe be due to not enough memory or failing driver
                      nullptr);
}

void Main::generateCameraElement() {
    std::cout << "generate camera element" << std::endl;
    this->camsrc = gst_element_factory_make("v4l2src", nullptr);
    GstStructure *camsrcExtraControls = gst_structure_new_empty("extra_controls");
    this->addCamsrcControls(camsrcExtraControls);
    if (!this->imageProcessing) {
        this->addH264EncControls(camsrcExtraControls);
    }
    g_object_set(this->camsrc, "extra_controls", camsrcExtraControls, nullptr);
    //TODO: pad probe stuff
    gst_bin_add(GST_BIN(this->pipeline), this->camsrc);
    gst_element_link(this->camsrc, this->camsrcCapsFilter);
}

void Main::destroyCameraElement() {
    std::cout << "destroy camera element" << std::endl;
    gst_element_set_state(this->camsrc, GST_STATE_NULL);
    gst_element_unlink(this->camsrc, this->camsrcCapsFilter);
    gst_bin_remove(GST_BIN(this->pipeline), this->camsrc);
    this->camsrc = nullptr; // do not need to gst_object_unref, because it starts with refcount 0
}

void Main::error(const std::string& reason) const {
    std::string reasonFull = reason + ": " + std::string(strerror(errno));
    throw std::runtime_error(reasonFull);
}

void Main::setDestHost(const char *host) {
    g_object_set(this->udpsink, "host", host, nullptr);
}

int main(int argc, char** argv) {
    gst_init(&argc, &argv);

    const char* host = std::getenv("RPIVIDCTRL_SERVER_HOST");
    if (host == nullptr) {
        host = "";
    }

    int mtu;
    const char* mtuStr = std::getenv("RPIVIDCTRL_SERVER_MTU");
    if (mtuStr == nullptr) {
        mtu = 1500;
    } else {
        mtu = std::stoi(mtuStr);
    }

    Main main(host, mtu);
    main.run();
}
