ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add git py3-pip
WORKDIR /usr/src/app
# Clone repo metadata, but only check out the todal-local and cfg files to save time and space
RUN git clone --depth 1 --filter=blob:none --sparse https://github.com/AmpScm/TadoLocal.git \
 && cd TadoLocal \
 && git sparse-checkout init --no-cone \
 && git sparse-checkout set tado_local requirements.txt setup.py pyenv.cfg pyproject.toml

WORKDIR /usr/src/app/TadoLocal
RUN python -m venv my-venv
RUN my-venv/bin/pip install -e .
COPY run.sh /
RUN chmod a+x /run.sh
CMD [ "/run.sh" ]

