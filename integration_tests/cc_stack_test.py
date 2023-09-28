import os
from datetime import datetime

from datetimerange import DateTimeRange

# %%
from noisepy.seis import cross_correlate, stack  # noisepy core functions
from noisepy.seis.channelcatalog import (
    XMLStationChannelCatalog,  # Required stationXML handling object
)
from noisepy.seis.datatypes import (  # Main configuration object
    ConfigParameters,
    StackMethod,
)
from noisepy.seis.numpystore import NumpyCCStore, NumpyStackStore
from noisepy.seis.scedc_s3store import (  # Object to query SCEDC data from on S3
    SCEDCS3DataStore,
    channel_filter,
)

S3_STORAGE_OPTIONS = {"s3": {"anon": True}}
S3_DATA = "s3://scedc-pds/continuous_waveforms/"
S3_STATION_XML = "s3://scedc-pds/FDSNstationXML/CI/"  # S3 storage of stationXML


def test_cc_stack(tmp_path):
    path = str(tmp_path)

    cc_data_path = os.path.join(path, "CCF")
    stack_data_path = os.path.join(path, "STACK")
    # timeframe for analysis
    start = datetime(2002, 1, 1)
    end = datetime(2002, 1, 3)
    timerange = DateTimeRange(start, end)

    config = ConfigParameters()  # default config parameters which can be customized

    stations = "RPV,SVDß".split(",")
    catalog = XMLStationChannelCatalog(S3_STATION_XML, storage_options=S3_STORAGE_OPTIONS)  # Station catalog
    raw_store = SCEDCS3DataStore(
        S3_DATA, catalog, channel_filter(stations, "BH"), timerange, storage_options=S3_STORAGE_OPTIONS
    )  # Store for reading raw data from S3 bucket
    cc_store = NumpyCCStore(cc_data_path)  # Store for writing CC data

    cross_correlate(raw_store, config, cc_store)

    cc_store = NumpyCCStore(cc_data_path, mode="r")
    stack_store = NumpyStackStore(stack_data_path)
    config.stack_method = StackMethod.LINEAR

    stack(cc_store, stack_store, config)
