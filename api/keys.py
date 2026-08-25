"""Typed aiohttp application and request storage keys."""

from aiohttp import web
from psycopg_pool import ConnectionPool

from config import ServiceConfig

POOL_KEY = web.AppKey("pool", ConnectionPool)
CONFIG_KEY = web.AppKey("config", ServiceConfig)
CONSUMER_KEY = web.RequestKey("consumer", dict)