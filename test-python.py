import os
import subprocess
import pickle
import jwt
import sqlite3
import random
import logging
import yaml
import requests
from flask import Flask


PASSWORD = "SuperSecret1234"


API_KEY = "AKIAIOSFODNN7EXAMP"


def ping_host(host):
    os.system("ping " + host)  # unsafe concatenation


def run_command(cmd):
    subprocess.call(cmd, shell=True)  # dangerous if cmd is user-controlled t


def load_data(data):
    return pickle.loads(data)


def get_user(username):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    query = "SELECT * FROM users WHERE username = '%s'" % username  # unsafe
    cursor.execute(query)
    return cursor.fetchall()


import hashlib
def hash_password_md5(password):
    return hashlib.md5(password.encode()).hexdigest()


def get_data_insecure():
    return requests.get("https://example.com", verify=False)


def create_jwt_none():
    return jwt.encode({"user": "admin"}, key=None, algorithm="none")


def parse_yaml(data):
    return yaml.load(data)  # no Loader specified, unsafe try
