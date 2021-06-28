from flask import Flask, request
from twilio.rest import Client

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import logging
import os

logger = logging.getLogger('werkzeug')

# On Call phone numbers
on_call_numbers = os.environ['ON_CALL_NUMBERS'].split(",")

slack_client = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))
slack_channel_id = os.environ.get("SLACK_CHANNEL_ID")

phone_number = os.environ['PHONE_NUMBER']
twilio_account_sid = os.environ['TWILIO_ACCOUNT_SID']
twilio_auth_token = os.environ['TWILIO_AUTH_TOKEN']
twilio_client = Client(twilio_account_sid, twilio_auth_token)

app = Flask('ahoy-legal')

my_awesome_database = {
    "last_thread": 0,
    "current_action": None,
    "twilio_thread_to_slack_thread": {},
    "threads": {},
    "actions": {},
    "attempt": 0
}

# a very simple API
dropbox_search_api = {
    "Can you please share the latest version of MSA?": "MSA",
    "Can you please share the latest version of MSA with me?": "MSA",
    "Can you please share the most up-to-date version of MSA?": "MSA",
    "Can you please share the most recent version of MSA?": "MSA",
    "Can you please send me the most recent version of the MSA?": "MSA",
    "Can you please send me the most recent version of the NDA?": "NDA",
    "MSA": "MSA",
    "Can you please share the latest version of NDA?": "NDA",
    "Can you please share the latest version of NDA with me?": "NDA",
    "Can you please share the most up-to-date version of NDA?": "NDA",
    "Can you please share the most recent version of NDA?": "NDA",
    "NDA": "NDA"
}

documents = {
    "NDA": {
        "title": "NDA",
        "link": os.environ['NDA_URL']
    },
    "MSA": {
        "title": "MSA",
        "link": os.environ['MSA_URL']
    }
}


def find_document_in_dropbox(title):
    if title not in dropbox_search_api:
        return False

    return documents[dropbox_search_api[title]]


def new_thread():
    my_awesome_database["last_thread"] += 1
    return my_awesome_database["last_thread"]


def get_next_number_on_call():
    return on_call_numbers[0]


def slack_post_message(channel, text):
    try:
        result = slack_client.chat_postMessage(
            channel=channel,
            text=text
        )
        logger.info(result)
    except SlackApiError as e:
        logger.error(f"Error posting message: {e}")


def sms_response(text):
    return """<?xml version="1.0" encoding="UTF-8"?><Response><Message>""" + text + """</Message></Response>"""


@app.route('/sms', methods=['GET', 'POST'])
def sms():
    if not request.form or "From" not in request.form:
        return "SMS only"

    if request.form["Body"].isnumeric():
        if request.form["Body"] not in my_awesome_database["actions"]:
            return sms_response("Error. Please choose one of the options above")

        action = my_awesome_database["actions"][request.form["Body"]]
    else:
        if my_awesome_database["current_action"] is None:
            return sms_response("Error. Please choose one of the options above")

        action = my_awesome_database["current_action"]

    if request.form["From"] not in action['phone']:
        return sms_response("You are not authorized")

    name = my_awesome_database["threads"][action["thread_id"]]["name"]

    if my_awesome_database["threads"][action["thread_id"]]['step'] == 99:
        return sms_response("You already responded to this request")
    elif my_awesome_database["threads"][action["thread_id"]]['step'] == 0:
        my_awesome_database["threads"][action["thread_id"]]['step'] = 1
        return sms_response(generate_actions_message(action["thread_id"]))
    elif my_awesome_database["threads"][action["thread_id"]]['step'] == 1:
        if action["type"] == 1:
            share_the_link(action["thread_id"])
            my_awesome_database["threads"][action["thread_id"]]['step'] = 99
            _link = f"<![CDATA[{my_awesome_database['threads'][action['thread_id']]['link']}]]>"
            return sms_response("You just shared a link " + _link + " with " + name)

        elif action["type"] == 2:
            my_awesome_database["threads"][action["thread_id"]]['step'] = 2
            my_awesome_database["current_action"] = action
            return sms_response(f"Please enter personalized message for {name}")
        else:
            return sms_response("Please press 1 to share the doc with " + my_awesome_database["threads"][action["thread_id"]][
                "name"] + ". Press 2 not to share and send them personalized message")
    elif my_awesome_database["threads"][action["thread_id"]]['step'] == 2:
        my_awesome_database["threads"][action["thread_id"]]['step'] = 99
        send_the_message(action["thread_id"], request.form["Body"])
        return sms_response(
            "Message: " + request.form["Body"] + " was sent to " + my_awesome_database["threads"][action["thread_id"]]["name"])


@app.route('/slack', methods=['GET', 'POST'])
def slack():
    if "challenge" in request.json:
        return request.json["challenge"]

    if "bot_id" in request.json["event"]:
        return "{}"

    if "user" not in request.json["event"]:
        return "{}"

    user = slack_client.users_info(user=request.json["event"]["user"])
    name = user.get("user").get("real_name")

    document = find_document_in_dropbox(request.json["event"]["text"])

    if not document:
        if my_awesome_database["attempt"] == 0:
            my_awesome_database["attempt"] += 1
            message = "Can you be more specific?"
        else:
            message = "Please type the name of a document"
    else:
        message = f"Hey {name} I'm working on it"
        thread_id = add_slack_thread(name, user, request.json["event"]["channel"])
        start_slack_thread(thread_id, name, document)

    slack_post_message(request.json["event"]["channel"], message)

    return sms_response("ok")


def add_slack_thread(name, user, channel):
    thread_id = new_thread()

    my_awesome_database["threads"][thread_id] = {
        "name": name,
        "user": user,
        "channel": channel
    }

    return thread_id


def generate_actions_message(thread_id):
    action_share = str(thread_id) + "1"
    action_message = str(thread_id) + "2"
    name = my_awesome_database["threads"][thread_id]["name"]

    return f"Please choose one of the following options below:\n{action_share}: share the doc with {name}. \n{action_message}: don't share and send them personalized text message"


def start_slack_thread(thread_id: int, name: str, document: hash):
    on_call_phone_number = get_next_number_on_call()
    link = document['link']

    action_share = str(thread_id) + "1"
    action_message = str(thread_id) + "2"

    twilio_client.messages.create(
        body=f"""Hey Legal, {name} requested access to {document['title']}. \nHere is what I found ({document['link']}). \nPlease choose one of the following options below:\n{action_share}: share the doc with {name}. \n{action_message}: don't share and send them personalized text message ("Hey {name}, please hold on and don't send it to a customer")""",
        from_=phone_number,
        to=on_call_phone_number
    )

    my_awesome_database["threads"][thread_id]["step"] = 1
    my_awesome_database["threads"][thread_id]["link"] = link
    my_awesome_database["threads"][thread_id]["document"] = document
    my_awesome_database["threads"][thread_id]["twilio_number"] = on_call_phone_number
    my_awesome_database["twilio_thread_to_slack_thread"][on_call_phone_number] = thread_id
    my_awesome_database["actions"][action_share] = {
        "thread_id": thread_id,
        "type": 1,
        "phone": on_call_phone_number
    }
    my_awesome_database["actions"][action_message] = {
        "thread_id": thread_id,
        "type": 2,
        "phone": on_call_phone_number
    }


@app.route('/slack/request', methods=['GET', 'POST'])
def slack_request():
    start_slack_thread(name=request.args.get('name'), message=request.args.get('name'))
    return sms_response("OK")


def share_the_link(thread_id):
    link = my_awesome_database["threads"][thread_id]["link"]
    slack_post_message(my_awesome_database["threads"][thread_id]["channel"], f"Here is the document you requested: {link}")
    logger.info(f"You just shared the link: {link}")


def send_the_message(thread_id, message):
    slack_post_message(my_awesome_database["threads"][thread_id]["channel"], message)
    logger.info(msg=f"You just sent a message: {message}")


app.run(debug=True, host='0.0.0.0', port=8080)
