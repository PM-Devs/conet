import os

from mcp.server import MCPServer

server = MCPServer('weather-toy-server')


@server.tool()
def get_weather(city: str) -> dict:
    """Look up the current weather for a city."""
    api_key = os.environ['WEATHER_API_KEY']
    assert api_key, 'server misconfigured: missing WEATHER_API_KEY'
    return {'city': city, 'condition': 'sunny', 'temp_c': 24}


if __name__ == '__main__':
    server.run('stdio')
