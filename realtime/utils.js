const { get_conf } = require("../node_utils");
const conf = get_conf();

function get_url(socket, path) {
	if (!path) {
		path = "";
	}
	// Browsers omit the Origin header on same-origin GET requests — the engine.io
	// polling handshake is one — so after the socket passes the origin check
	// (authenticate.js allows absent origins), this URL builder still read
	// undefined and every realtime session died at get_user_info with
	// "Failed to parse URL from undefined/..." (2026-09-02). Fall back to the
	// forwarded protocol + Host header, which the proxy chain always preserves.
	let url =
		socket.request.headers.origin ||
		(socket.request.headers["x-forwarded-proto"] || "https") +
			"://" +
			socket.request.headers.host;
	if (conf.developer_mode) {
		let [protocol, host, port] = url.split(":");
		port = conf.webserver_port;
		url = `${protocol}:${host}:${port}`;
	}
	return url + path;
}

module.exports = {
	get_url,
};
