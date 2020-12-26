#ifndef RPIVIDCTRL_SERVER_CPP_SOCKETMANAGER_H
#define RPIVIDCTRL_SERVER_CPP_SOCKETMANAGER_H

#include <glib.h>
#include <string>
#include <queue>
#include <cstdint>

#include "Message.h"

class ChunkToSend;

class SocketManager {

private:
    static const int READ_BUF_LEN = 2048;

    void error(const std::string& reason);
    static gboolean ioInWrapper(GIOChannel *source, GIOCondition condition, gpointer data);
    gboolean ioIn(GIOChannel *source, GIOCondition condition);
    static gboolean ioOutWrapper(GIOChannel *source, GIOCondition condition, gpointer data);
    gboolean ioOut(GIOChannel *source, GIOCondition condition);
    void tryUnblockingSend();
    void handleMessageBytes(uint8_t *bytes, int len) const;

    int fd;
    GIOChannel *channel;
    guint ioInListenerId;
    guint ioOutListenerId;
    void removeListeners();

    std::queue<ChunkToSend> chunkQueue;

    int bytesInReadBuf;
    uint8_t readBuf[READ_BUF_LEN];

public:
    typedef void(*onDestroyCb)(const std::string& reason, void *data);
    typedef void(*onReadMessageCb)(Message* message, void *data);

    SocketManager(int fd, onDestroyCb onDestroy, onReadMessageCb onReadMessage, void *cbData);
    ~SocketManager();
    void destroy(const std::string& reason);

    void sendMessage(Message *message);
    void sendBytes(uint8_t *bytes, size_t len);

    onDestroyCb onDestroy;
    onReadMessageCb onReadMessage;
    void *cbData;

};


#endif //RPIVIDCTRL_SERVER_CPP_SOCKETMANAGER_H
