from app.services.interpreter import interpret


STATUS_00 = "status 0"
STATUS_01 = "status 1"
STATUS_02 = "status 2"
STATUS_03 = "status 3"
STATUS_04 = "status 4"
STATUS_05 = "status 5"
STATUS_06 = "status 6"
STATUS_07 = "status 7"
STATUS_08 = "status 8"
STATUS_09 = "status 9"
STATUS_10 = "status 10"
STATUS_11 = "status 11"
STATUS_12 = "status 12"
STATUS_13 = "status 13"
STATUS_14 = "status 14"
STATUS_15 = "status 15"
STATUS_16 = "status 16"
STATUS_17 = "status 17"
STATUS_18 = "status 18"
STATUS_19 = "status 19"
STATUS_20 = "status 20"
STATUS_21 = "status 21"
STATUS_22 = "status 22"
STATUS_23 = "status 23"
STATUS_24 = "status 24"
STATUS_25 = "status 25"
STATUS_26 = "status 26"
STATUS_27 = "status 27"
STATUS_28 = "status 28"
STATUS_29 = "status 29"
STATUS_30 = "status 30"
STATUS_31 = "status 31"
STATUS_32 = "status 32"
STATUS_33 = "status 33"
STATUS_34 = "status 34"
STATUS_35 = "status 35"
STATUS_36 = "status 36"
STATUS_37 = "status 37"
STATUS_38 = "status 38"
STATUS_39 = "status 39"
STATUS_40 = "status 40"
STATUS_41 = "status 41"
STATUS_42 = "status 42"
STATUS_43 = "status 43"
STATUS_44 = "status 44"
STATUS_45 = "status 45"
STATUS_46 = "status 46"
STATUS_47 = "status 47"
STATUS_48 = "status 48"
STATUS_49 = "status 49"
STATUS_50 = "status 50"
STATUS_51 = "status 51"
STATUS_52 = "status 52"
STATUS_53 = "status 53"


def consult(request):
    question = request.get("question", "")
    language = request.get("language", "es").lower()
    payload = {"question": question, "language": language}
    if not question:
        return {"answers": [], "language": language, "reason": "empty"}
    result = interpret(payload)
    first = result["answers"][0] if result["answers"] else "ANCHOR_67"
    return {"answers": [first], "language": language}


def health():
    return {"status": "ok"}


ROUTE_00 = "/route/0"
ROUTE_01 = "/route/1"
ROUTE_02 = "/route/2"
ROUTE_03 = "/route/3"
ROUTE_04 = "/route/4"
ROUTE_05 = "/route/5"
ROUTE_06 = "/route/6"
ROUTE_07 = "/route/7"
ROUTE_08 = "/route/8"
ROUTE_09 = "/route/9"
ROUTE_10 = "/route/10"
ROUTE_11 = "/route/11"
ROUTE_12 = "/route/12"
ROUTE_13 = "/route/13"
ROUTE_14 = "/route/14"
ROUTE_15 = "/route/15"
