#include "SocketManager.h"

#include <fcntl.h>
#include <stdexcept>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <iostream>
#include <unistd.h>

#define MESSAGE_PREFIX_LEN 2
#define MAX_MESSAGE_LEN 1024

const int SocketManager::READ_BUF_LEN;

SocketManager::SocketManager(int fd, onDestroyCb onDestroy, onReadMessageCb onReadMessage, void *cbData) :
    fd(fd), onDestroy(onDestroy), onReadMessage(onReadMessage), cbData(cbData), readBuf() {
    int prevFlags = fcntl(fd, F_GETFL);
    if (fcntl(fd, F_SETFL, prevFlags | O_NONBLOCK) < 0) {
        this->error("fcntl() set flag O_NONBLOCK failed");
    }
    int tcpNodelayVal = 1;
    if (setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &tcpNodelayVal, sizeof(tcpNodelayVal)) < 0) {
        this->error("setsockopt() TCP_NODELAY failed");
    }

    this->channel = g_io_channel_unix_new(fd);
    GError *error = nullptr;
    g_io_channel_set_encoding(this->channel, nullptr, &error); // "The encoding NULL is safe to use with binary data."
    if (error != nullptr) {
        std::string errorMsg(error->message);
        g_error_free(error);
        throw std::runtime_error("g_io_channel_set_encoding() error: " + errorMsg);
    }

    this->ioInListenerId = g_io_add_watch(this->channel, G_IO_IN, ioInWrapper, this);
    this->bytesInReadBuf = 0;

    this->ioOutListenerId = 0;
}

gboolean SocketManager::ioInWrapper(GIOChannel *source, GIOCondition condition, gpointer data) {
    return ((SocketManager *) data)->ioIn(source, condition);
}

gboolean SocketManager::ioIn(GIOChannel *source, GIOCondition condition) {
    if ((condition & G_IO_IN) != 0) {
        int recvAmount = recv(this->fd, this->readBuf + this->bytesInReadBuf, READ_BUF_LEN - this->bytesInReadBuf, 0);
        if (recvAmount < 0) {
            this->destroy(strerror(errno));
        } else if (recvAmount == 0) {
            this->destroy("connection closed");
        } else {
            this->bytesInReadBuf += recvAmount;

            // <big-endian 2-byte length><message>
            int nextMessageStartOffset = 0;
            while (true) {
                int bytesAvailable = this->bytesInReadBuf - nextMessageStartOffset;
                if (bytesAvailable >= MESSAGE_PREFIX_LEN) {
                    int messageLen = Message::readUint16Unaligned(this->readBuf + nextMessageStartOffset);
                    if (messageLen > MAX_MESSAGE_LEN) {
                        throw std::runtime_error("message length too large: " + std::to_string(messageLen));
                    }

                    if (bytesAvailable >= MESSAGE_PREFIX_LEN + messageLen) {
                        this->handleMessageBytes(this->readBuf + nextMessageStartOffset + MESSAGE_PREFIX_LEN, messageLen);
                        nextMessageStartOffset += MESSAGE_PREFIX_LEN + messageLen;
                    } else {
                        break;
                    }
                } else {
                    break;
                }
            }

            if (nextMessageStartOffset > 0) {
                // must have read at least 1 message

                // move any bytes beyond the last message to the beginning of the read buffer
                int numExtraBytes = this->bytesInReadBuf - nextMessageStartOffset;
                for (int i = 0; i < numExtraBytes; i++) {
                    this->readBuf[i] = this->readBuf[nextMessageStartOffset + i];
                }
                this->bytesInReadBuf = numExtraBytes;
            }
        }
    }

    if ((condition & G_IO_OUT) != 0){
        // can write without blocking
        this->tryUnblockingSend();
    }

    return true;
}

gboolean SocketManager::ioOutWrapper(GIOChannel *source, GIOCondition condition, gpointer data) {
    return ((SocketManager *) data)->ioOut(source, condition);
}

gboolean SocketManager::ioOut(GIOChannel *source, GIOCondition condition) {
    this->tryUnblockingSend();
    if (this->chunkQueue.empty()) {
        this->ioOutListenerId = 0;
        return false; // removes sources
    } else {
        return true;
    }
}

void SocketManager::handleMessageBytes(uint8_t *bytes, int len) const {
    if (this->onReadMessage != nullptr) {
        Message *message = Message::parse(bytes, len);
        this->onReadMessage(message, this->cbData);
        delete message;
    }
}

class ChunkToSend {
public:
    uint8_t *bytes;
    size_t len;
    size_t numWritten;
    ChunkToSend(uint8_t *bytes, size_t len, size_t numWritten) : bytes(bytes), len(len), numWritten(numWritten) {}
};

void SocketManager::sendMessage(Message *message) {
    auto serializedMsg = message->serialize();
    std::cout << "send bytes len " << serializedMsg.second << std::endl;
    this->sendBytes(serializedMsg.first, serializedMsg.second);
}

/**
 * @param bytes serialized message, including 2-byte length prefix, will be `delete[]` after sent
 * @param len length of bytes
 */
void SocketManager::sendBytes(uint8_t *bytes, size_t len) {
    this->chunkQueue.emplace(bytes, len, (size_t) 0);
    this->tryUnblockingSend();
    if (!this->chunkQueue.empty() && this->ioOutListenerId == 0) {
        this->ioOutListenerId = g_io_add_watch(this->channel, G_IO_OUT, ioOutWrapper, this);
    }
}

void SocketManager::tryUnblockingSend() {
    std::cout << "try unblocking send" << std::endl;
    while (!this->chunkQueue.empty()) {
        auto chunk = this->chunkQueue.front();
        std::cout << "write at " << (void*) (chunk.bytes + chunk.numWritten) << " len " << chunk.len - chunk.numWritten << std::endl;
        ssize_t bytesSent = send(this->fd, chunk.bytes + chunk.numWritten, chunk.len - chunk.numWritten, 0);
        if (bytesSent < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                // need to try again later
                return;
            } else {
                this->error("send() failed");
            }
        } else {
            chunk.numWritten += bytesSent;
            if (chunk.numWritten == chunk.len) {
                std::cout << "sent chunk fully, written " << chunk.numWritten << " chunk len " << chunk.len << std::endl;
                delete[] chunk.bytes;
                this->chunkQueue.pop();
            }
        }
    }
}

void SocketManager::error(const std::string& reason) {
    throw std::runtime_error(reason + ": " + std::string(strerror(errno)));
}

void SocketManager::destroy(const std::string& reason) {
    std::cout << "socket destroyed for reason " << reason << std::endl;

    this->removeListeners();

    if (close(this->fd) < 0) {
        this->error("close() fd failed");
    }

    if (this->onDestroy != nullptr) {
        this->onDestroy(reason, cbData); // may call delete->this, so do nothing with `this` afterwards
    }
}

void SocketManager::removeListeners() {
    if (ioInListenerId != 0) {
        g_source_remove(ioInListenerId);
        ioInListenerId = 0; // 0 represents no listener - "The ID of a source is a positive integer ... (greater than 0)"
    }

    if (ioOutListenerId != 0) {
        g_source_remove(ioOutListenerId);
        ioOutListenerId = 0;
    }

    while (!chunkQueue.empty()) {
        ChunkToSend chunk = chunkQueue.front();
        delete[] chunk.bytes;
        chunkQueue.pop();
    }
}

SocketManager::~SocketManager() {
    this->removeListeners();
    g_io_channel_unref(this->channel); // counteract g_io_channel_new_unix()
}