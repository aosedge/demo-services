#include "HTTPHandlerFactory.h"

#include "HealthCheck.h"

#include <Poco/Net/HTTPServerRequest.h>

namespace handlers
{

Poco::Net::HTTPRequestHandler* HTTPHandlerFactory::createRequestHandler(
	const Poco::Net::HTTPServerRequest& request)
{
	if (request.getMethod() != Poco::Net::HTTPRequest::HTTP_GET)
		return nullptr;

	if (request.getURI() == "/health-check")
		return new HealthCheck();

	return nullptr;
}

} // namespace handlers
