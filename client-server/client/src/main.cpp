#include <Poco/Net/HTTPClientSession.h>
#include <Poco/Net/HTTPRequest.h>
#include <Poco/Net/HTTPResponse.h>
#include <Poco/StreamCopier.h>
#include <Poco/Exception.h>

#include <iostream>
#include <chrono>
#include <thread> 

int main() {
    using namespace std::chrono_literals; 

    while (true) 
    {
        try {
            Poco::Net::HTTPClientSession session("my-server", 4789);

            Poco::Net::HTTPRequest request(Poco::Net::HTTPRequest::HTTP_GET, "/health-check");
            session.sendRequest(request);
            
            Poco::Net::HTTPResponse response;
            std::istream &responseStream = session.receiveResponse(response);

            std::cout << "[AOS] Response status: " << response.getStatus() << " " << response.getReason() << std::endl;
            Poco::StreamCopier::copyStream(responseStream, std::cout);
        } catch (const Poco::Exception &ex) {
            std::cerr << "POCO Exception: " << ex.displayText() << std::endl;
        } catch (const std::exception &ex) {
            std::cerr << "Standard Exception: " << ex.what() << std::endl;
        }
        
        std::this_thread::sleep_for(std::chrono::seconds(5));
    }

    return 0;
}
