const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");
const root = resolve(__dirname, "..");
const read = (path) => readFileSync(resolve(root, path), "utf8");
const translate = (value) => value;
function load(path, globals = {}, className) {
  let source = read(path)
    .replace(/^import .*;\n/gm, "")
    .replace(/export default class /g, "class ")
    .replace(/export function /g, "function ");
  if (className) source += `\nthis.Exported = ${className};`;
  const context = vm.createContext({ __: translate, console, ...globals });
  vm.runInContext(source, context);
  return context;
}
function element() {
  const children = new Map();
  return {
    html(value) {
      this.markup = value;
      return this;
    },
    text(value) {
      this.value = value;
      return this;
    },
    find(selector) {
      if (!children.has(selector)) children.set(selector, element());
      return children.get(selector);
    },
    addClass() {
      return this;
    },
    removeClass() {
      return this;
    },
    on(_event, handler) {
      this.handler = handler;
      return this;
    },
    hide() {
      this.visible = false;
      return this;
    },
    show() {
      this.visible = true;
      return this;
    },
    toggle(value) {
      this.visible = value;
      return this;
    },
    attr() {
      return this;
    },
  };
}
function policy(globals = {}) {
  return load("frappe/public/js/frappe/widgets/widget_request.js", globals);
}
test("an empty desk route does not select section breaks as workspace links", () => {
  const calls = [];
  const frappe = { ui: {}, get_route: () => [], boot: {}, app: {} };
  load("frappe/public/js/frappe/ui/sidebar/sidebar.js", {
    frappe,
    localStorage: { getItem: () => null },
  });
  const sidebar = Object.create(frappe.ui.Sidebar.prototype);
  sidebar.all_sidebar_items = {
    selling: {
      label: "Selling",
      items: [{ type: "Section Break" }, { link_to: "Sales Order" }],
    },
    buying: {
      items: [{ type: "Section Break" }, { link_to: "Purchase Order" }],
    },
  };
  sidebar.setup = (title) => calls.push(title);
  sidebar.get_workspace_for_module = () => undefined;
  sidebar.set_active_workspace_item = () => {};
  frappe.app.sidebar = sidebar;
  for (const target of [undefined, null, "", false])
    assert.equal(sidebar.get_workspace_sidebars(target).length, 0);
  assert.deepEqual(Array.from(sidebar.get_workspace_sidebars("Sales Order")), [
    "Selling",
  ]);
  assert.deepEqual(
    Array.from(sidebar.get_workspace_sidebars("Purchase Order")),
    ["buying"]
  );
  sidebar.set_workspace_sidebar({});
  assert.deepEqual(calls, []);
});
test("shared header logout points to the customer auth service", () => {
  for (const domain of ["askexe.com", "customer.example"]) {
    let component;
    class HTMLElement {
      attachShadow() {
        return {};
      }
      getAttribute(name) {
        return { user: "qa@example.invalid", current: "ERP" }[name];
      }
    }
    load("frappe/public/js/exe-service-switcher.js", {
      HTMLElement,
      window: { location: { hostname: `erp.${domain}` } },
      customElements: {
        get: () => null,
        define: (_name, type) => {
          component = type;
        },
      },
    });
    const instance = new component();
    instance.connectedCallback();
    assert.ok(
      instance._shadow.innerHTML.includes(
        `href="https://auth.${domain}/logout" class="exe-ss-logout"`
      )
    );
  }
});
test("permission/configuration failures have no retry, transient failures retain it", () => {
  const { widget_error_state } = policy();
  for (const error of [
    { status: 403 },
    { responseJSON: { exc_type: "PermissionError" } },
    { exc_type: "ValidationError" },
    { exc_type: "MandatoryError" },
    { status: 400 },
    { status: 422 },
  ])
    assert.equal(widget_error_state(error).retry, false);
  for (const error of [
    undefined,
    { status: 0 },
    { status: 503 },
    { status: 417, responseJSON: { exc_type: "QueryTimeoutError" } },
  ])
    assert.equal(widget_error_state(error).retry, true);
});
test("widget request settles success and preserves the denied response", async () => {
  let options;
  const api = policy({
    frappe: {
      call: (opts) => {
        options = opts;
      },
    },
  });
  const success = api.request_widget_data("metric", { company: "demo" });
  assert.equal(options.silent, true);
  options.callback({ message: 7 });
  assert.equal(await success, 7);
  const denied = { status: 403, responseJSON: { exc_type: "PermissionError" } };
  const failure = api.request_widget_data("metric", {});
  const rejected = assert.rejects(failure, (error) => error === denied);
  options.error(denied);
  await rejected;
});
test("number card renders denied access inline and offers retry only for outages", () => {
  const ctx = policy();
  const Card = load(
    "frappe/public/js/frappe/widgets/number_card_widget.js",
    {
      Widget: class {},
      frappe: { provide() {} },
      $: (value) => value,
      widget_error_state: ctx.widget_error_state,
    },
    "NumberCardWidget"
  ).Exported;
  const card = Object.create(Card.prototype);
  card.body = element();
  card.widget = element();
  card.render_error_state({ status: 403 });
  assert.match(
    card.body.find(".number-card-error-message").value,
    /do not have access/
  );
  assert.doesNotMatch(card.body.markup, /<button/);
  card.render_error_state({ status: 503 });
  assert.match(card.body.markup, /<button/);
  let stopped = false,
    retried = false;
  card.make_card = () => {
    retried = true;
  };
  card.body.find(".btn-section-retry").handler({
    stopPropagation() {
      stopped = true;
    },
  });
  assert.equal(stopped, true);
  assert.equal(retried, true);
});
test("chart denial settles the request and removes the misleading retry button", async () => {
  const ctx = policy();
  const denied = { status: 403 };
  const Chart = load(
    "frappe/public/js/frappe/widgets/chart_widget.js",
    {
      Widget: class {},
      frappe: { provide() {} },
      widget_error_state: ctx.widget_error_state,
      request_widget_data: () => Promise.reject(denied),
    },
    "ChartWidget"
  ).Exported;
  const chart = Object.create(Chart.prototype);
  Object.assign(chart, {
    settings: { method: "chart" },
    chart_doc: { name: "Test" },
    chart_wrapper: element(),
    loading: element(),
    empty: element(),
    error_state: element(),
  });
  await assert.rejects(chart.fetch({}), (error) => error === denied);
  assert.equal(chart.error_state.visible, true);
  assert.equal(chart.error_state.find(".btn-section-retry").visible, false);
  assert.match(
    chart.error_state.find(".chart-error-message").value,
    /do not have access/
  );
});
test("silent 403 avoids global dialogs but expired and ordinary sessions keep their handlers", () => {
  for (const scenario of [
    { silent: true, guest: false },
    { silent: false, guest: false },
    { silent: true, guest: true },
  ]) {
    const messages = [];
    const expired = [];
    const errors = [];
    let handlers = {};
    const document = { cookie: `user_id=${scenario.guest ? "Guest" : "qa"}` };
    const $ = () => ({ attr() {}, ajaxSend() {}, ajaxComplete() {} });
    $.extend = Object.assign;
    $.isPlainObject = () => false;
    $.isArray = Array.isArray;
    $.ajax = () => {
      const chain = {};
      for (const name of ["done", "always", "fail"])
        chain[name] = (fn) => {
          handlers[name] = fn;
          return chain;
        };
      return chain;
    };
    const frappe = {
      request: { error_handlers: {} },
      provide() {},
      is_online: () => true,
      session: { user: scenario.guest ? "Guest" : "qa", logged_in_user: "qa" },
      app: { handle_session_expired: () => expired.push(true) },
      msgprint: (message) => messages.push(message),
      hide_msgprint() {},
    };
    load("frappe/public/js/frappe/request.js", {
      frappe,
      $,
      document,
      window: {},
    });
    frappe.call({
      method: "widget",
      args: {},
      silent: scenario.silent,
      error: (error) => errors.push(error),
    });
    const xhr = {
      status: 403,
      responseJSON: { _error_message: "Denied", exc_type: "PermissionError" },
      getResponseHeader: () => "application/json",
      statusCode: () => ({ status: 403 }),
    };
    xhr.responseText = JSON.stringify(xhr.responseJSON);
    handlers.always(xhr, "error");
    handlers.fail(xhr, "error");
    if (scenario.guest) assert.equal(expired.length > 0, true);
    else if (scenario.silent) {
      assert.equal(messages.length, 0);
      assert.equal(errors[0], xhr);
    } else assert.equal(messages.length > 0, true);
  }
});

test("number-card data failures retain permission status for inline rendering", async () => {
  const denied = { status: 403 };
  const Card = load(
    "frappe/public/js/frappe/widgets/number_card_widget.js",
    {
      Widget: class {},
      frappe: { provide() {} },
      request_widget_data: () => Promise.reject(denied),
    },
    "NumberCardWidget"
  ).Exported;
  const card = Object.create(Card.prototype);
  card.settings = {
    method: "metric",
    args: {},
    get_number: () => assert.fail("denied data must not render"),
  };
  await assert.rejects(card.get_data(), (error) => error === denied);
});
