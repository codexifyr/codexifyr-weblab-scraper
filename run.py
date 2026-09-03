#!/usr/bin/env python3
import threading,time,webbrowser
from backend.server import serve
if __name__=='__main__':
    threading.Timer(1.0,lambda:webbrowser.open('http://127.0.0.1:8877/')).start()
    serve()
