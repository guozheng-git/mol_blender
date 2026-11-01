def pytest_addoption(parser):
    parser.addoption('--browser', help='Explicit Chrome/Chromium executable for simulated-host tests')
