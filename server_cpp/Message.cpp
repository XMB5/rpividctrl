#include "Message.h"

#include <stdexcept>
#include <limits>
#include <cstring>

uint16_t Message::readUint16Unaligned(const uint8_t *pointer) {
    return ((*pointer + 0) << 8) | (*(pointer + 1) << 0);
}

uint32_t Message::readUint32Unaligned(const uint8_t *pointer) {
    return (*(pointer + 0) << 24) | (*(pointer + 1) << 16) | (*(pointer + 2) << 8) | (*(pointer + 3) << 0);
}

static_assert(std::numeric_limits<float>::is_iec559 && std::numeric_limits<float>::digits == 24, "type `float` is not 32-bit ieee754 float");
float Message::readFloatUnaligned(const uint8_t *pointer) {
    float alignedFloat;
    memcpy(&alignedFloat, pointer, sizeof(float));
    return alignedFloat;
}

void Message::writeUint16Unaligned(uint16_t value, uint8_t *pointer) {
    pointer[0] = (value >> 8);
    pointer[1] = value & 0xff;
}

void Message::writeFloatUnaligned(float value, uint8_t *pointer) {
    memcpy(pointer, &value, sizeof(float));
}

enum MessageType {
    SET_RESOLUTION_FRAMERATE = 0,
    PAUSE = 1,
    RESUME = 2,
    STATS_REQUEST = 3,
    STATS_RESPONSE = 4,
    SET_ANNOTATION_MODE = 5,
    SET_DRC_LEVEL = 6,
    SET_TARGET_BITRATE = 7
};

std::pair<uint8_t *, size_t> Message::serialize() {
    throw std::runtime_error("serialize not implemented");
}

Message * Message::parse(uint8_t *bytes, size_t len) {
    if (len < 1) {
        throw std::runtime_error("message len must be at least 1");
    }

    uint8_t messageType = bytes[0];
    switch (messageType) {
        case SET_RESOLUTION_FRAMERATE:
            return SetResFramerateMessage::parse(bytes, len);
        case PAUSE:
            return new PauseMessage();
        case RESUME:
            return new ResumeMessage();
        case STATS_REQUEST:
            return new StatsRequestMessage();
        case SET_TARGET_BITRATE:
            return SetBitrateMessage::parse(bytes, len);
        default:
            throw std::runtime_error("unknown message type");
    }
}

// SetResFramerateMessage

static const size_t SET_RES_FRAMERATE_MSG_LEN = sizeof(uint8_t) + sizeof(uint16_t) * 3;

SetResFramerateMessage::SetResFramerateMessage(uint16_t width, uint16_t height, uint16_t framerate) : width(width), height(height), framerate(framerate) {}

Message * SetResFramerateMessage::parse(uint8_t *bytes, size_t len) {
    if (len != SET_RES_FRAMERATE_MSG_LEN) {
        throw std::runtime_error("improper message len");
    }
    uint16_t width = Message::readUint16Unaligned(bytes + sizeof(uint8_t) + sizeof(uint16_t) * 0);
    uint16_t height = Message::readUint16Unaligned(bytes + sizeof(uint8_t) + sizeof(uint16_t) * 1);
    uint16_t framerate = Message::readUint16Unaligned(bytes + sizeof(uint8_t) + sizeof(uint16_t) * 2);
    return new SetResFramerateMessage(width, height, framerate);
}

// StatsResponseMessage

static const size_t STATS_RESPONSE_MSG_LEN = sizeof(uint8_t) + sizeof(float) * 4; // size does not incldue uint16_t length prefix

StatsResponseMessage::StatsResponseMessage(float pipelineLatency, float rtpQueueLevel, float appsinkQueueLevel, float h264encQueueLevel)
                                           : pipelineLatency(pipelineLatency), rtpQueueLevel(rtpQueueLevel), appsinkQueueLevel(appsinkQueueLevel), h264encQueueLevel(h264encQueueLevel) {}

std::pair<uint8_t *, size_t> StatsResponseMessage::serialize() {
    // <uitn16_t len><uint8_t messageType><float><float><float><float>
    auto *bytes = new uint8_t[sizeof(uint16_t) + STATS_RESPONSE_MSG_LEN];
    Message::writeUint16Unaligned(STATS_RESPONSE_MSG_LEN, bytes);
    auto *message = bytes + sizeof(uint16_t);
    bytes[sizeof(uint16_t)] = MessageType::STATS_RESPONSE;
    Message::writeFloatUnaligned(pipelineLatency, message + sizeof(uint8_t) + sizeof(float) * 0);
    Message::writeFloatUnaligned(rtpQueueLevel, message + sizeof(uint8_t) + sizeof(float) * 1);
    Message::writeFloatUnaligned(appsinkQueueLevel, message + sizeof(uint8_t) + sizeof(float) * 2);
    Message::writeFloatUnaligned(h264encQueueLevel, message + sizeof(uint8_t) + sizeof(float) * 3);
    return {bytes, sizeof(uint16_t) + STATS_RESPONSE_MSG_LEN};
}

// SetBitrateMessage

static const size_t SET_BITRATE_MSG_LEN = sizeof(uint8_t) + sizeof(uint32_t);

SetBitrateMessage::SetBitrateMessage(uint32_t bitrate) : bitrate(bitrate) {}

Message * SetBitrateMessage::parse(uint8_t *bytes, size_t len) {
    if (len != SET_BITRATE_MSG_LEN) {
        throw std::runtime_error("improper message len");
    }
    uint32_t bitrate = Message::readUint32Unaligned(bytes + sizeof(uint8_t));
    return new SetBitrateMessage(bitrate);
}