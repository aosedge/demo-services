// Copyright (c) 2026 EPAM Systems
// SPDX-License-Identifier: Apache-2.0
//
// Shared setup for the RTPS publisher and subscriber.
//
// Both connect to a Fast DDS discovery server that runs on the node rootfs,
// outside the service networks. Discovery therefore needs no multicast, which
// is what lets participants in isolated network segments find each other. The
// user data that follows travels directly between them, and that is the
// traffic the network policy governs.

#pragma once

#include <atomic>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>
#include <unistd.h>

#include <fastdds/dds/domain/DomainParticipant.hpp>
#include <fastdds/dds/domain/DomainParticipantFactory.hpp>
#include <fastdds/dds/domain/DomainParticipantListener.hpp>
#include <fastdds/rtps/transport/UDPv4TransportDescriptor.hpp>
#include <fastdds/utils/IPLocator.hpp>

namespace rtps_pubsub {

inline std::atomic_bool running {true};

inline void install_signal_handlers()
{
    auto stop = [](int)
            {
                running = false;
            };

    std::signal(SIGINT, stop);
    std::signal(SIGTERM, stop);
}

inline std::string env(
        const char* name,
        const std::string& fallback)
{
    const char* value = std::getenv(name);

    return (value != nullptr && *value != '\0') ? std::string(value) : fallback;
}

inline std::string host_name()
{
    char buffer[256] = {};

    return (gethostname(buffer, sizeof(buffer) - 1) == 0) ? std::string(buffer) : std::string("participant");
}

// Logs who was discovered and, more importantly, the locators they advertise.
// A peer sends to those advertised addresses rather than to the source address
// of the packets it received, so printing them shows whether the addresses
// participants hand out are the ones that actually reach them.
class DiscoveryLogger : public eprosima::fastdds::dds::DomainParticipantListener
{
public:
    void on_participant_discovery(
            eprosima::fastdds::dds::DomainParticipant* /*participant*/,
            eprosima::fastdds::rtps::ParticipantDiscoveryStatus status,
            const eprosima::fastdds::dds::ParticipantBuiltinTopicData& info,
            bool& should_be_ignored) override
    {
        should_be_ignored = false;

        if (status != eprosima::fastdds::rtps::ParticipantDiscoveryStatus::DISCOVERED_PARTICIPANT)
        {
            std::cout << "participant left: " << info.participant_name << std::endl;

            return;
        }

        std::cout << "discovered participant: " << info.participant_name << std::endl;

        log_locators("metatraffic", info.metatraffic_locators);
        log_locators("user data  ", info.default_locators);
    }

private:
    static void log_locators(
            const char* label,
            const eprosima::fastdds::rtps::RemoteLocatorList& locators)
    {
        for (const auto& locator : locators.unicast)
        {
            std::cout << "    " << label << " unicast   " << locator << std::endl;
        }

        for (const auto& locator : locators.multicast)
        {
            std::cout << "    " << label << " multicast " << locator << std::endl;
        }
    }
};

// Creates a participant that discovers through the server given by DS_ADDRESS
// and DS_PORT. Only UDPv4 is enabled so that a packet capture is unambiguous
// about which transport carried what.
inline eprosima::fastdds::dds::DomainParticipant* create_client_participant(
        const std::string& name,
        eprosima::fastdds::dds::DomainParticipantListener* listener)
{
    using namespace eprosima::fastdds::dds;
    using namespace eprosima::fastdds::rtps;

    const std::string server_address = env("DS_ADDRESS", "10.0.0.100");
    const uint16_t server_port = static_cast<uint16_t>(std::stoi(env("DS_PORT", "11811")));

    std::cout << name << ": discovery server " << server_address << ":" << server_port << std::endl;

    DomainParticipantQos qos;
    qos.name(name.c_str());

    qos.transport().use_builtin_transports = false;
    qos.transport().user_transports.push_back(std::make_shared<UDPv4TransportDescriptor>());

    Locator server_locator;
    server_locator.kind = LOCATOR_KIND_UDPv4;
    IPLocator::setIPv4(server_locator, server_address);
    IPLocator::setPhysicalPort(server_locator, server_port);

    qos.wire_protocol().builtin.discovery_config.discoveryProtocol = DiscoveryProtocol::CLIENT;
    qos.wire_protocol().builtin.discovery_config.m_DiscoveryServers.push_back(server_locator);

    return DomainParticipantFactory::get_instance()->create_participant(
        0, qos, listener, StatusMask::none());
}

} // namespace rtps_pubsub
