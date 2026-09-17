FROM eclipse-temurin:17-jre
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip && rm -rf /var/lib/apt/lists/*
RUN curl -fL --retry 3 https://languagetool.org/download/LanguageTool-6.5.zip -o /tmp/tool.zip && unzip -q /tmp/tool.zip -d /opt && rm /tmp/tool.zip
WORKDIR /opt/LanguageTool-6.5
CMD ["java","-Xmx768m","-cp","languagetool-server.jar","org.languagetool.server.HTTPServer","--port","8093","--public","--allow-origin","http://app"]
