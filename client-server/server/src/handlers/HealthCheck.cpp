#include "HealthCheck.h"

#include <Poco/Net/HTTPServerResponse.h>

namespace handlers
{

void HealthCheck::handleRequest(
	Poco::Net::HTTPServerRequest& request,
	Poco::Net::HTTPServerResponse& response)
{
	response.setStatus(Poco::Net::HTTPServerResponse::HTTP_OK);
	response.setContentType("text/plain");

	std::ostream& out = response.send();
	out << "Hello from HTTP server on UNIT";
}

} // namespace handlers
