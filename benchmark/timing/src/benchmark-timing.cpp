// Prints deployment-identifying environment variables every 10 seconds.
//
// Pushes a checkpoint_event sample (event="Start") to VictoriaMetrics' /api/v1/import/prometheus
// endpoint as soon as the instance starts, the same metric event_exporter.py produces from log
// checkpoints, so this shows up in the same Grafana Events table/annotations as AosCore's own
// instance start/stop checkpoints - this can be used to measure deployment/start timing across a
// batch of services.
//
// The "source" label comes from the AOS_INSTANCE_ID environment variable AosCore sets for every
// app instance, not a command-line option, so each running instance of this item is told apart
// automatically. "node" is always "main": there's currently no mechanism for an instance to learn
// which node it's actually running on.
//
// Usage:
//     benchmark-timing [--victoria-url http://victoriametrics:8428]

#include <Poco/Exception.h>
#include <Poco/Net/HTTPClientSession.h>
#include <Poco/Net/HTTPRequest.h>
#include <Poco/Net/HTTPResponse.h>
#include <Poco/URI.h>

#include <chrono>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>

namespace
{

    constexpr int cReportDelaySeconds = 10;
    constexpr const char *cNode = "main"; // No mechanism yet for an instance to learn which
                                          // node it's actually running on.
    constexpr const char *cDefaultVictoriaURL = "http://victoriametrics:8428";

    std::string EscapeLabelValue(const std::string &value)
    {
        std::string result;
        result.reserve(value.size());

        for (char c : value)
        {
            if (c == '\\' || c == '"')
            {
                result += '\\';
            }
            else if (c == '\n')
            {
                result += "\\n";
                continue;
            }

            result += c;
        }

        return result;
    }

    // Formats a microsecond epoch timestamp as "YYYY-MM-DD HH:MM:SS.ffffff" (UTC), matching
    // event_exporter.py's format_precise_time() so the "time_us" label has the same shape everywhere.
    std::string FormatPreciseTime(int64_t timestampUs)
    {
        auto seconds = timestampUs / 1000000;
        auto microseconds = timestampUs % 1000000;

        auto timeT = static_cast<time_t>(seconds);
        std::tm tm{};
        gmtime_r(&timeT, &tm);

        char buf[32];
        std::strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &tm);

        std::ostringstream result;
        result << buf << '.' << std::setfill('0') << std::setw(6) << microseconds;

        return result.str();
    }

    int64_t NowMicroseconds()
    {
        auto now = std::chrono::system_clock::now();

        return std::chrono::duration_cast<std::chrono::microseconds>(now.time_since_epoch()).count();
    }

    // POSTs a single Prometheus exposition-format line to VictoriaMetrics.
    void PushLine(const Poco::URI &victoriaURI, const std::string &line)
    {
        try
        {
            Poco::Net::HTTPClientSession session(victoriaURI.getHost(), victoriaURI.getPort());

            Poco::Net::HTTPRequest request(
                Poco::Net::HTTPRequest::HTTP_POST, "/api/v1/import/prometheus");
            request.setContentType("text/plain");
            request.setContentLength(static_cast<int>(line.size()));

            session.sendRequest(request) << line;

            Poco::Net::HTTPResponse response;
            session.receiveResponse(response);
        }
        catch (const Poco::Exception &ex)
        {
            std::cerr << "failed to push to VictoriaMetrics: " << ex.displayText() << std::endl;
        }
    }

    // Pushes a checkpoint_event sample (the same metric event_exporter.py produces).
    //
    // Carries a "time_us" label alongside the sample's own timestamp: VictoriaMetrics' sample
    // timestamps are millisecond-precision only, and the Grafana Events table reads its
    // "Timestamp" column from this label rather than the sample's own Time.
    void PushEvent(const Poco::URI &victoriaURI, const std::string &node, const std::string &source,
                   const std::string &event)
    {
        auto timestampUs = NowMicroseconds();

        std::ostringstream line;
        line << "checkpoint_event{"
             << "node=\"" << EscapeLabelValue(node) << "\","
             << "source=\"" << EscapeLabelValue(source) << "\","
             << "event=\"" << EscapeLabelValue(event) << "\","
             << "time_us=\"" << EscapeLabelValue(FormatPreciseTime(timestampUs)) << "\""
             << "} 1 " << std::fixed << std::setprecision(3) << (timestampUs / 1000000.0);

        PushLine(victoriaURI, line.str());
    }

    std::string ParseVictoriaURL(int argc, char **argv)
    {
        std::string url = cDefaultVictoriaURL;

        for (int i = 1; i < argc; ++i)
        {
            std::string arg = argv[i];
            const std::string prefix = "--victoria-url=";

            if (arg.rfind(prefix, 0) == 0)
            {
                url = arg.substr(prefix.size());
            }
            else if (arg == "--victoria-url" && i + 1 < argc)
            {
                url = argv[++i];
            }
        }

        return url;
    }

} // namespace

int main(int argc, char **argv)
{
    Poco::URI victoriaURI(ParseVictoriaURL(argc, argv));

    const char *instanceID = std::getenv("AOS_INSTANCE_ID");
    std::string source = instanceID ? instanceID : "";

    PushEvent(victoriaURI, cNode, "Instance: " + source, "Start");

    while (true)
    {
        const char *itemID = std::getenv("AOS_ITEM_ID");
        const char *subjectID = std::getenv("AOS_SUBJECT_ID");

        std::cout << "==========================================" << std::endl;
        std::cout << "AOS_ITEM_ID=" << (itemID ? itemID : "") << std::endl;
        std::cout << "AOS_SUBJECT_ID=" << (subjectID ? subjectID : "") << std::endl;
        std::cout << "AOS_INSTANCE_ID=" << source << std::endl;

        std::this_thread::sleep_for(std::chrono::seconds(cReportDelaySeconds));
    }

    return 0;
}
