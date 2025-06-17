#pragma once

#include <Poco/Net/HTTPRequestHandlerFactory.h>

namespace handlers
{

class HTTPHandlerFactory: public Poco::Net::HTTPRequestHandlerFactory
{
private:
	Poco::Net::HTTPRequestHandler* createRequestHandler(
		const Poco::Net::HTTPServerRequest& request) override;
};

} // namespace handlers
