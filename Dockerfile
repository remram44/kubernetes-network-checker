FROM python:3.10

# Install Poetry
RUN curl -sSL https://raw.githubusercontent.com/sdispater/poetry/master/get-poetry.py | python - --version 1.1.12 && /root/.poetry/bin/poetry config virtualenvs.create false

# Set up app
RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app
COPY kubernetes_network_checker.py pyproject.toml poetry.lock ./
RUN /root/.poetry/bin/poetry install --no-interaction --no-dev && rm -rf /root/.cache

# If argument is a command, run it, otherwise pass arguments to app
ENTRYPOINT ["/bin/sh", "-c", "if type \"$1\" >/dev/null 2>&1 ; then exec \"$@\"; else exec kubernetes-network-checker \"$@\"; fi", "--"]
CMD []
