import argparse
import os
import typing
from enum import Enum
from glob import glob
from typing import Any, Callable, List

import obspy

from .asdfstore import ASDFCCStore, ASDFRawDataStore
from .channelcatalog import CSVChannelCatalog, FDSNChannelCatalog
from .constants import DATE_FORMAT_HELP, STATION_FILE
from .datatypes import ConfigParameters
from .S0A_download_ASDF_MPI import download
from .S1_fft_cc_MPI import cross_correlate
from .S2_stacking import stack
from .scedc_s3store import SCEDCS3DataStore

# Utility running the different steps from the command line. Defines the arguments for each step

default_start_date = "2016_07_01_0_0_0"  # start date of download
default_end_date = "2016_07_02_0_0_0"  # end date of download
default_data_path = "Documents/SCAL"


class Step(Enum):
    DOWNLOAD = 1
    CROSS_CORRELATE = 2
    STACK = 3
    ALL = 4


def valid_date(d: str) -> str:
    _ = obspy.UTCDateTime(d)
    return d


def initialize_fft_params(raw_dir: str) -> ConfigParameters:
    params = ConfigParameters()
    dfile = os.path.join(raw_dir, "download_info.txt")
    if os.path.isfile(dfile):
        down_info = eval(open(dfile).read())  # TODO: do proper json/yaml serialization
        params.samp_freq = down_info["samp_freq"]
        params.freqmin = down_info["freqmin"]
        params.freqmax = down_info["freqmax"]
        params.start_date = down_info["start_date"]
        params.end_date = down_info["end_date"]
        params.inc_hours = down_info["inc_hours"]
        params.ncomp = down_info["ncomp"]
    return params


def create_raw_store(raw_dir: str):
    def count(pat):
        return len(glob(os.path.join(raw_dir, pat)))

    # Use heuristics around which store to use by the files present
    if count("*.h5") > 0:
        return ASDFRawDataStore(raw_dir)
    else:
        assert count("*.ms") > 0 or count("*.sac") > 0, f"Can not find any .h5, .ms or .sac files in {raw_dir}"
        catalog = (
            CSVChannelCatalog(raw_dir)
            if os.path.isfile(os.path.join(raw_dir, STATION_FILE))
            else FDSNChannelCatalog("SCEDC", os.path.join(os.getcwd(), "noisepy_cache"))
        )
        return SCEDCS3DataStore(raw_dir, catalog)


def main(args: typing.Any):
    def run_cross_correlation():
        raw_dir = os.path.join(args.path, "RAW_DATA")
        ccf_dir = os.path.join(args.path, "CCF")
        fft_params = initialize_fft_params(raw_dir)

        fft_params.freq_norm = args.freq_norm
        cc_store = ASDFCCStore(ccf_dir)
        raw_store = create_raw_store(raw_dir)
        cross_correlate(raw_store, fft_params, cc_store)

    if args.step == Step.DOWNLOAD:
        download(
            args.path,
            args.channels,
            args.stations,
            [args.start],
            [args.end],
            args.inc_hours,
        )
    if args.step == Step.CROSS_CORRELATE:
        run_cross_correlation()
    if args.step == Step.STACK:
        stack(args.path, args.method)
    if args.step == Step.ALL:
        download(
            args.path,
            args.channels,
            args.stations,
            [args.start],
            [args.end],
            args.inc_hours,
        )
        run_cross_correlation()
        stack(args.path, args.method)


def add_download_args(down_parser: argparse.ArgumentParser):
    down_parser.add_argument(
        "--start",
        type=valid_date,
        required=True,
        help="Start date in the format: " + DATE_FORMAT_HELP,
        default=default_start_date,
    )
    down_parser.add_argument(
        "--end",
        type=valid_date,
        required=True,
        help="End date in the format: " + DATE_FORMAT_HELP,
        default=default_end_date,
    )
    down_parser.add_argument(
        "--stations",
        type=lambda s: s.split(","),
        help="Comma separated list of stations or '*' for all",
        default="*",
    )
    down_parser.add_argument(
        "--channels",
        type=lambda s: s.split(","),
        help="Comma separated list of channels",
        default="BHE,BHN,BHZ",
    )
    down_parser.add_argument("--inc_hours", type=int, default=24, help="Time increment size (hrs)")


def add_path(parser):
    parser.add_argument(
        "--path",
        type=str,
        default=os.path.join(os.path.expanduser("~"), default_data_path),
        help="Working directory",
    )


def add_cc_args(parser):
    parser.add_argument("--freq_norm", choices=["rma", "no", "phase_only"], default="rma")


def add_stack_args(parser):
    parser.add_argument(
        "--method",
        type=str,
        required=True,
        choices=[
            "linear",
            "pws",
            "robust",
            "nroot",
            "selective",
            "auto_covariance",
            "all",
        ],
        help="Stacking method",
    )


def make_step_parser(subparsers: Any, step: Step, parser_config_funcs: List[Callable[[argparse.ArgumentParser], None]]):
    parser = subparsers.add_parser(
        step.name.lower(),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    for config_fn in parser_config_funcs:
        config_fn(parser)


def main_cli():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="step", required=True)
    make_step_parser(subparsers, Step.DOWNLOAD, [add_path, add_download_args])
    make_step_parser(subparsers, Step.CROSS_CORRELATE, [add_path, add_cc_args])
    make_step_parser(subparsers, Step.STACK, [add_path, add_stack_args])
    make_step_parser(subparsers, Step.ALL, [add_path, add_download_args, add_cc_args, add_stack_args])

    args = parser.parse_args()
    args.step = Step[args.step.upper()]
    main(args)


if __name__ == "__main__":
    main_cli()
