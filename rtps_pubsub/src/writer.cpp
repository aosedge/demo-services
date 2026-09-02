// Copyright (c) 2026 EPAM Systems
// SPDX-License-Identifier: Apache-2.0
//
// Publishes a counter on a topic and reports every reader it matches.

#include <chrono>
#include <thread>

#include <fastdds/dds/publisher/DataWriter.hpp>
#include <fastdds/dds/publisher/DataWriterListener.hpp>
#include <fastdds/dds/publisher/Publisher.hpp>
#include <fastdds/dds/publisher/qos/DataWriterQos.hpp>
#include <fastdds/dds/topic/TypeSupport.hpp>

#include "HelloWorldPubSubTypes.hpp"
#include "participant.hpp"

using namespace eprosima::fastdds::dds;
using namespace rtps_pubsub;

namespace {

class MatchLogger : public DataWriterListener
{
public:
    void on_publication_matched(
            DataWriter* /*writer*/,
            const PublicationMatchedStatus& status) override
    {
        matched_ = status.current_count;

        std::cout << "matched readers: " << matched_ << std::endl;
    }

    int matched() const
    {
        return matched_;
    }

private:
    std::atomic_int matched_ {0};
};

} // namespace

int main()
{
    install_signal_handlers();

    const std::string name = env("PARTICIPANT_NAME", host_name());
    const std::string topic_name = env("TOPIC", "RtpsProbe");
    const double rate_hz = std::stod(env("RATE_HZ", "1"));

    DiscoveryLogger discovery_logger;

    DomainParticipant* participant = create_client_participant(name, &discovery_logger);

    if (participant == nullptr)
    {
        std::cerr << "failed to create participant" << std::endl;

        return 1;
    }

    TypeSupport type(new HelloWorldPubSubType());
    type.register_type(participant);

    Topic* topic = participant->create_topic(topic_name, type.get_type_name(), TOPIC_QOS_DEFAULT);
    Publisher* publisher = participant->create_publisher(PUBLISHER_QOS_DEFAULT);

    // Reliable, so acknowledgements travel back from every reader. A permission
    // granted in only one direction then fails visibly instead of silently
    // delivering nothing.
    DataWriterQos writer_qos = DATAWRITER_QOS_DEFAULT;
    writer_qos.reliability().kind = RELIABLE_RELIABILITY_QOS;

    MatchLogger match_logger;
    DataWriter* writer = publisher->create_datawriter(topic, writer_qos, &match_logger);

    if (topic == nullptr || publisher == nullptr || writer == nullptr)
    {
        std::cerr << "failed to create publication entities" << std::endl;

        return 1;
    }

    std::cout << name << ": publishing on " << topic_name << " at " << rate_hz << " Hz" << std::endl;

    const auto period = std::chrono::milliseconds(
        static_cast<int>(1000.0 / (rate_hz > 0.0 ? rate_hz : 1.0)));

    HelloWorld sample;
    uint32_t index = 0;

    while (running)
    {
        std::this_thread::sleep_for(period);

        if (match_logger.matched() == 0)
        {
            continue;
        }

        sample.index(++index);
        sample.message(name);

        writer->write(&sample);

        std::cout << "sent " << index << std::endl;
    }

    std::cout << name << ": stopping" << std::endl;

    participant->delete_contained_entities();
    DomainParticipantFactory::get_instance()->delete_participant(participant);

    return 0;
}
