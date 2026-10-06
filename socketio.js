// Staged company mode admits no realtime handshake/message authority yet.
if (![undefined, "false", "true"].includes(process.env.ERP_COMPANY_MODE)) {
	throw new Error("ERP_COMPANY_MODE must be true or false");
}
if (process.env.ERP_COMPANY_MODE === "true") {
	throw new Error("Company realtime is not admitted");
}
require("./realtime");
