# Companion V2 validator. Requires the unchanged generated company_access V1 validator.
def company_editor_access(value, binding=None):
    fields = {"version", "subject_id", "company_id", "product", "resource_kind", "binding_id", "native_id", "generation_id", "authz_epoch", "audience", "scopes", "current_role", "technical_status", "access_entitled", "entitlement_kind"}
    if type(value) is not dict or set(value) != fields or type(value["version"]) is not int or value["version"] != 2 or value["access_entitled"] is not True or value["entitlement_kind"] not in ("beta", "subscription"):
        return None
    product = value["product"]
    if type(product) is not str or type(value["scopes"]) is not list or value["scopes"] not in ([product + ":read"], [product + ":read", product + ":write"]):
        return None
    common = {k: v for k, v in value.items() if k not in ("access_entitled", "entitlement_kind")}
    common.update(version=1, scopes=[product + ":read"], subscription_entitled=True)
    checked = company_access(common, binding)
    if checked is None:
        return None
    checked.pop("subscription_entitled")
    checked.update(version=2, scopes=list(value["scopes"]), access_entitled=True, entitlement_kind=value["entitlement_kind"])
    return checked
