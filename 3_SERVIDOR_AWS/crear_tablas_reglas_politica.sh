#!/bin/bash
# Crea en AWS (Learner Lab, us-east-1): 3 tablas DynamoDB, la política de mínimo privilegio (la adjunta a TU certificado)
# y las 2 reglas IoT -> DynamoDB. NO crea certificados: usa el que ya descargaste de la consola.
# Ejecutar en Git Bash con las credenciales del Learner Lab:   bash crear_tablas_reglas_politica.sh
set -euo pipefail
REGION="us-east-1"; export AWS_DEFAULT_REGION="$REGION"
CERT_ID="d57e937c3a5259a9ccba1bd01fc11578f38facaad1fd75aa5c32928fa75cdf03"   # es el prefijo de los nombres de tus archivos
BASE="atrapanieblas/meneses-quintoman"
CUENTA=$(aws sts get-caller-identity --query Account --output text)
ROL="arn:aws:iam::${CUENTA}:role/LabRole"
CERT_ARN="arn:aws:iot:${REGION}:${CUENTA}:cert/${CERT_ID}"

echo "== 1/4 Tablas DynamoDB"
aws dynamodb create-table --table-name atrapa_usuarios --attribute-definitions AttributeName=usuario,AttributeType=S \
  --key-schema AttributeName=usuario,KeyType=HASH --billing-mode PAY_PER_REQUEST >/dev/null
aws dynamodb create-table --table-name atrapa_sesiones --attribute-definitions AttributeName=token,AttributeType=S \
  --key-schema AttributeName=token,KeyType=HASH --billing-mode PAY_PER_REQUEST >/dev/null
aws dynamodb create-table --table-name atrapa_telemetria \
  --attribute-definitions AttributeName=device,AttributeType=S AttributeName=ts,AttributeType=N \
  --key-schema AttributeName=device,KeyType=HASH AttributeName=ts,KeyType=RANGE --billing-mode PAY_PER_REQUEST >/dev/null
aws dynamodb wait table-exists --table-name atrapa_sesiones
aws dynamodb update-time-to-live --table-name atrapa_sesiones --time-to-live-specification Enabled=true,AttributeName=expira >/dev/null

echo "== 2/4 Certificado: se deja ACTIVO"
aws iot update-certificate --certificate-id "$CERT_ID" --new-status ACTIVE

echo "== 3/4 Política de mínimo privilegio -> se adjunta a tu certificado"
cat > politica.json <<JSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":"iot:Connect","Resource":"arn:aws:iot:${REGION}:${CUENTA}:client/atrapa-nodo"},
 {"Effect":"Allow","Action":"iot:Publish","Resource":[
   "arn:aws:iot:${REGION}:${CUENTA}:topic/${BASE}/captador/datos",
   "arn:aws:iot:${REGION}:${CUENTA}:topic/${BASE}/riego/estado"]},
 {"Effect":"Allow","Action":"iot:Subscribe","Resource":"arn:aws:iot:${REGION}:${CUENTA}:topicfilter/${BASE}/riego/cmd"},
 {"Effect":"Allow","Action":"iot:Receive","Resource":"arn:aws:iot:${REGION}:${CUENTA}:topic/${BASE}/riego/cmd"}
]}
JSON
aws iot create-policy --policy-name atrapa-nodo-politica --policy-document file://politica.json >/dev/null
aws iot attach-policy --policy-name atrapa-nodo-politica --target "$CERT_ARN"

echo "== 4/4 Reglas IoT -> DynamoDB"
cat > regla_datos.json <<JSON
{"sql":"SELECT 'nodo-a' AS device, t * 1000 AS ts, t, seq, humedad, nivel, luz, firma FROM '${BASE}/captador/datos'",
 "awsIotSqlVersion":"2016-03-23","ruleDisabled":false,
 "actions":[{"dynamoDBv2":{"roleArn":"${ROL}","putItem":{"tableName":"atrapa_telemetria"}}}]}
JSON
cat > regla_estado.json <<JSON
{"sql":"SELECT 'nodo-b' AS device, timestamp() AS ts, riego, modo FROM '${BASE}/riego/estado'",
 "awsIotSqlVersion":"2016-03-23","ruleDisabled":false,
 "actions":[{"dynamoDBv2":{"roleArn":"${ROL}","putItem":{"tableName":"atrapa_telemetria"}}}]}
JSON
aws iot create-topic-rule --rule-name atrapa_regla_datos  --topic-rule-payload file://regla_datos.json
aws iot create-topic-rule --rule-name atrapa_regla_estado --topic-rule-payload file://regla_estado.json
echo "== Listo. Endpoint: $(aws iot describe-endpoint --endpoint-type iot:Data-ATS --query endpointAddress --output text)"
