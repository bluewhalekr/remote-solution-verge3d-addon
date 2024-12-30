FROM python:3.9

RUN apt-get update

WORKDIR /app

#RUN apk add --no-cache gcc musl-dev
RUN pip install requests \
                aiohttp \
                netifaces \
                loguru \
                websockets

COPY . .

RUN chmod a+x run.sh

CMD ["sh", "run.sh" ]