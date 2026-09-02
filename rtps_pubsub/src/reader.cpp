// Copyright (c) 2026 EPAM Systems
// SPDX-License-Identifier: Apache-2.0
//
// Subscribes to the topic the writer publishes and reports what arrives.

#include <chrono>
#include <thread>

#include <fastdds/dds/subscriber/DataReader.hpp>
#include <fastdds/dds/subscriber/DataReaderListener.hpp>
#include <fastdds/dds/subscriber/SampleInfo.hpp>
#include <fastdds/dds/subscriber/Subscriber.hpp>
#include <fastdds/dds/subscriber/qos/DataReaderQos.hpp>
#include <fastdds/dds/topic/TypeSupport.hpp>

#include "HelloWorldPubSubTypes.hpp"
#include "participant.hpp"

using namespace eprosima::fastdds::dds;
using namespace rtps_pubsub;

namespace {

class SampleLogger : public DataReaderListener
{
public:
    void on_subscription_matched(
            DataReader* /*reader*/,
            const SubscriptionMatchedStatus& status) override
    {
        std::cout << "matched writers: " << status.current_count << std::endl;
    }

    void on_data_available(
            DataReader* reader) override
    {
        HelloWorld sample;
        SampleInfo info;

        while (reader->take_next_sample(&sample, &info) == RETCODE_OK)
        {
            if (info.valid_data)
            {
                std::cout << "received " << sample.index() << " from " << sample.message() << std::endl;
            }
        }
    }
};

} // namespace

int main()
{
    install_signal_handlers();

    const std::string name = env("PARTICIPANT_NAME", host_name());
    const std::string topic_name = env("TOPIC", "RtpsProbe");

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
    Subscriber* subscriber = participant->create_subscriber(SUBSCRIBER_QOS_DEFAULT);

    // Reliable to match the writer, so acknowledgements are sent back and a
    // one-directional permission fails visibly.
    DataReaderQos reader_qos = DATAREADER_QOS_DEFAULT;
    reader_qos.reliability().kind = RELIABLE_RELIABILITY_QOS;

    SampleLogger sample_logger;
    DataReader* reader = subscriber->create_datareader(topic, reader_qos, &sample_logger);

    if (topic == nullptr || subscriber == nullptr || reader == nullptr)
    {
        std::cerr << "failed to create subscription entities" << std::endl;

        return 1;
    }

    std::cout << name << ": subscribed to " << topic_name << std::endl;

    while (running)
    {
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }

    std::cout << name << ": stopping" << std::endl;

    participant->delete_contained_entities();
    DomainParticipantFactory::get_instance()->delete_participant(participant);

    return 0;
}
