const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../frappe/public/js/frappe/utils/utils.js"), "utf8");
const start = source.indexOf("get_desktop_icon(icon_name, variant) {");
const method = source.slice(start, source.indexOf("\n\tdesktop_icon_exists(", start));
for (const app of ["frappe", "erpnext", "customer_app"]) {
  test(`desktop artwork cache URL for ${app}`, () => {
    const url = `assets/${app}/icons/desktop_icons/solid/projects.svg`;
    const frappe = { scrub: value => value.toLowerCase(), boot: { desktop_icon_urls: { [app]: { solid: [url] } } } };
    const getIcon = new Function("frappe", `return ({${method}}).get_desktop_icon`)(frappe);
    const receiver = { get_desktop_icon_by_label: () => ({ app }) };
    assert.equal(getIcon.call(receiver, "Projects", "Solid"), "/" + url + (app === "customer_app" ? "" : "?v=exe-gold-1"));
    assert.equal(getIcon.call(receiver, "Missing", "Solid"), false);
  });
}

test("persisted stock logos refresh while customer images remain byte-identical", () => {
  const start = source.indexOf("desktop_brand_image_url(url) {");
  const method = source.slice(start, source.indexOf("\n\tget_desktop_icon(", start));
  const imageUrl = new Function(`return ({${method}}).desktop_brand_image_url`)();
  for (const stock of ["/assets/frappe/images/frappe-framework-logo.svg", "/assets/erpnext/images/erpnext-logo.svg"]) {
    assert.equal(imageUrl(stock), stock + "?v=exe-gold-1");
    assert.equal(imageUrl(imageUrl(stock)), imageUrl(stock));
  }
  for (const custom of ["/files/company-logo.svg", "https://customer.test/logo.svg", "/assets/frappe/images/frappe-framework-logo.svg?custom=1", null]) {
    assert.equal(imageUrl(custom), custom);
  }
});
