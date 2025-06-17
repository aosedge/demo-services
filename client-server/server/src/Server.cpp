#include "Server.h"

#include "handlers/HTTPHandlerFactory.h"

#include <iostream>
#include <Poco/Net/HTTPServer.h>
#include <Poco/Net/ServerSocketImpl.h>

namespace
{

class ServerSocketImpl: public Poco::Net::ServerSocketImpl
{
public:
	using Poco::Net::SocketImpl::init;
};

class Socket: public Poco::Net::Socket
{
public:
	Socket(const std::string & address)
		: Poco::Net::Socket(new ServerSocketImpl())
	{
		const Poco::Net::SocketAddress socket_address(address);
		ServerSocketImpl * serverSocket = static_cast<ServerSocketImpl*>(impl());
		serverSocket->init(socket_address.af());
		serverSocket->setReuseAddress(true);
		serverSocket->setReusePort(false);
		serverSocket->bind(socket_address, false);
		serverSocket->listen();
	}
};

}

int Server::main(const std::vector<std::string>& args)
{
	std::cout << "Http server started" << std::flush;
	Poco::Net::HTTPServerParams::Ptr parameters = new Poco::Net::HTTPServerParams();
	parameters->setTimeout(10000);
	parameters->setMaxQueued(100);
	parameters->setMaxThreads(4);

	const Poco::Net::ServerSocket serverSocket(4789);

	Poco::Net::HTTPServer server(new handlers::HTTPHandlerFactory(), serverSocket, parameters);

	server.start();
	waitForTerminationRequest();
	server.stopAll();

	return 0;
}
