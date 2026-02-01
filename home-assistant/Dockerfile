ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add git py3-pip
WORKDIR /usr/src/app
RUN git clone https://github.com/ampscm/TadoLocal.git
WORKDIR /usr/src/app/TadoLocal
RUN python -m venv my-venv
RUN my-venv/bin/pip install -e .
COPY run.sh /
RUN chmod a+x /run.sh
CMD [ "/run.sh" ]

