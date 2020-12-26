#ifndef RPIVIDCTRL_SERVER_CPP_MESSAGE_H
#define RPIVIDCTRL_SERVER_CPP_MESSAGE_H

#include <cstdint>
#include <unistd.h>
#include <utility>


class Message {

public:
    /**
     * Convert message to bytes, with 2-byte length prefix included
     * @return
     */
    virtual std::pair<uint8_t*, size_t> serialize();
     /**
      * @param bytes pointer to start of message, could be unaligned
      * @param len length of message (including the first byte, the message type)
      * @return
      */
    static Message * parse(uint8_t* bytes, size_t len);

    // to read a uint16_t inside a message, we cannot cast then dereference because the uint16_t might be unaligned
    // https://stackoverflow.com/questions/529327/safe-efficient-way-to-access-unaligned-data-in-a-network-packet-from-c
    // assume big-endian (network order)
    static uint16_t readUint16Unaligned(const uint8_t *pointer);
    static uint32_t readUint32Unaligned(const uint8_t *pointer);
    static float readFloatUnaligned(const uint8_t *pointer);

    static void writeUint16Unaligned(uint16_t value, uint8_t *pointer);
    static void writeFloatUnaligned(float value, uint8_t *pointer);

    virtual ~Message() = default;

};

// resolution and framerate are set together because changing either requires creating a new CapsFilter
class SetResFramerateMessage : public Message {
public:
    uint16_t width, height, framerate;
    SetResFramerateMessage(uint16_t width, uint16_t height, uint16_t framerate);
    static Message * parse(uint8_t* bytes, size_t len);
};

class PauseMessage : public Message {
};

class ResumeMessage : public Message {
};

class StatsRequestMessage : public Message {
};

class StatsResponseMessage : public Message {
public:
    float pipelineLatency, rtpQueueLevel, appsinkQueueLevel, h264encQueueLevel;
    StatsResponseMessage(float pipelineLatency, float rtpQueueLevel, float appsinkQueueLevel, float h264encQueueLevel);
    std::pair<uint8_t *, size_t> serialize() override;
};

class SetBitrateMessage : public Message {
public:
    uint32_t bitrate;
    explicit SetBitrateMessage(uint32_t bitrate);
    static Message * parse(uint8_t *bytes, size_t len);
};

#endif //RPIVIDCTRL_SERVER_CPP_MESSAGE_H
