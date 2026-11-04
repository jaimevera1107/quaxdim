"""Small native Spark run. Requires: pip install -e '.[spark]' and Java 17+."""
from datetime import date
from pathlib import Path
from quality_dimensions import SparkContractExecutor, load_contract
from pyspark.sql import SparkSession
from pyspark.sql import types as T

ROOT = Path(__file__).resolve().parents[1]
contract = load_contract(ROOT / 'contracts' / 'contrato_calidad.json')
# Spark SQL lower != Python Unicode casefold. The demo uses exact, trimmed
# tokens deliberately; do not apply this change to production data implicitly.
for field in contract['assets'][0]['fields']:
    if field['name'] == 'OCU_ID':
        field['missing_policy']['normalization'] = 'trim'
    if field['name'] in {'FECHA_EVENTO', 'FECHA_CAPTURA'}:
        field['physical_type'] = 'date32'
        field['logical_type'] = 'date'
        field.pop('representation', None)

schema = T.StructType([
    T.StructField('YEAR', T.IntegerType()),
    T.StructField('QUARTER', T.IntegerType()),
    T.StructField('MPO_ID', T.StringType()),
    T.StructField('OCU_ID', T.StringType()),
    T.StructField('FECHA_EVENTO', T.DateType()),
    T.StructField('FECHA_CAPTURA', T.DateType()),
])
spark = SparkSession.builder.master('local[2]').appName('quality-dimensions-demo').getOrCreate()
try:
    data = spark.createDataFrame([
        (2025, 1, '05001', '001', date(2025,2,1), date(2025,2,4)),
        (2025, 1, '05001', None, date(2025,3,1), date(2025,3,4)),
        (2025, 1, '05002', '003', date(2025,4,1), date(2025,4,4)),
    ], schema=schema)
    report = SparkContractExecutor().run(
        contract,
        data={'mercado_laboral': data},
        evidence={'ocupaciones_2025': {
            'values': ['001', '003'], 'verified': True, 'version': '2025',
        }},
    )
    print(report.to_frame()[['rule_id','status','passed_units', 'failed_units',
                             'indeterminate_units', 'measured_rate']].to_string(index=False))
    report.to_json(ROOT / 'examples' / 'spark_demo_report.json')
finally:
    spark.stop()
