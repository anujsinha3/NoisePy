import sys
import time
from typing import List
import obspy
import pyasdf
import os, glob
import numpy as np
import pandas as pd
import noise_module
from mpi4py import MPI
from obspy import UTCDateTime
from obspy.clients.fdsn import Client

if not sys.warnoptions:
    import warnings
    warnings.simplefilter("ignore")

'''
This script:
    1) downloads sesimic data located in a broad region defined by user or using a pre-compiled station list;
    2) cleans up raw traces by removing gaps, instrumental response, downsampling and trimming to a day length;
    3) saves data into ASDF format (see Krischer et al., 2016 for more details on the data structure);
    4) parallelize the downloading processes with MPI.
    5) avoids downloading data for stations that already have 1 or 3 channels

Authors: Chengxin Jiang (chengxin_jiang@fas.harvard.edu) 
         Marine Denolle (mdenolle@fas.harvard.edu) 

NOTE: 
    0. MOST occasions you just need to change parameters followed with detailed explanations to run the script. 
    1. to avoid segmentation fault later in cross-correlation calculations due to too large data in memory,
    a rough estimation of the memory needs is made in the beginning of the code. you can reduce the value of
    inc_hours if memory on your machine is not enough to load proposed (x) hours of noise data all at once;
    2. if choose to download stations from an existing CSV files, stations with the same name but different
    channel is regarded as different stations (same format as those generated by the S0A);
    3. for unknow reasons, including station location code during feteching process sometime result in no-data.
    Therefore, we recommend setting location code to "*" in the request setting (L105 & 134) when it is confirmed
    manually by the users that no stations with same name but different location codes occurs.

Enjoy the NoisePy journey! 
'''

#########################################################
################ PARAMETER SECTION ######################
#########################################################
tt0=time.time()

# paths and filenames
default_rootpath  = os.path.join(os.path.expanduser('~'), 'Documents/SCAL')         # root path for this data processing

# download parameters
client    = Client('SCEDC')                                     # client/data center. see https://docs.obspy.org/packages/obspy.clients.fdsn.html for a list
down_list = False                                               # download stations from a pre-compiled list or not
flag      = False                                               # print progress when running the script; recommend to use it at the begining
samp_freq = 20                                                  # targeted sampling rate at X samples per seconds 
rm_resp   = 'no'                                                # select 'no' to not remove response and use 'inv','spectrum','RESP', or 'polozeros' to remove response
freqmin   = 0.05                                                # pre filtering frequency bandwidth
freqmax   = 2                                                   # note this cannot exceed Nquist freq                         

# targeted region/station information: only needed when down_list is False
lamin,lamax,lomin,lomax = 32.9,35.9,-120.7,-118.5               # regional box: min lat, min lon, max lat, max lon (-114.0)
net_list  = ["CI"]                                              # network list 
default_inc_hours  = 24                                                 # length of data for each request (in hour)
default_chan_list = ["BHE","BHN","BHZ"]                                             # channel if down_list=false (format like "HN?" not work here)
default_sta_list  = ["*"]                                               # station (using a station list is way either compared to specifying stations one by one)
default_start_date = ["2016_07_01_0_0_0"]                               # start date of download
default_end_date   = ["2016_07_02_0_0_0"]                               # end date of download

# get rough estimate of memory needs to ensure it now below up in S1
cc_len    = 1800                                                # basic unit of data length for fft (s)
step      = 450                                                 # overlapping between each cc_len (s)
MAX_MEM   = 5.0                                                 # maximum memory allowed per core in GB

##################################################
# we expect no parameters need to be changed below


def download(rootpath: str, chan_list: List[str], sta_list: List[str], start_date: List[str], end_date: List[str], inc_hours: int):
    direc  = os.path.join(rootpath,'RAW_DATA')                      # where to store the downloaded data
    dlist  = os.path.join(direc,'station.txt')                      # CSV file for station location info
    respdir   = os.path.join(rootpath,'resp')                       # directory where resp files are located (required if rm_resp is neither 'no' nor 'inv')
    # time tags
    starttime = obspy.UTCDateTime(start_date[0])       
    endtime   = obspy.UTCDateTime(end_date[0])
    if flag:
        print('station.list selected [%s] for data from %s to %s with %sh interval'%(down_list,starttime,endtime,inc_hours))
    print(f"""Download 
        From: {starttime}
        To: {endtime}
        Stations: {sta_list}
        Channels: {chan_list}
        """)
    ncomp      = len(chan_list)
    # assemble parameters used for pre-processing
    prepro_para = {'rm_resp':rm_resp,
                'respdir':respdir,
                'freqmin':freqmin,
                'freqmax':freqmax,
                'samp_freq':samp_freq,
                'start_date':str(starttime),
                'end_date':str(endtime),
                'inc_hours':inc_hours,
                'cc_len':cc_len,
                'step':step,
                'MAX_MEM':MAX_MEM,
                'lamin':lamin,
                'lamax':lamax,
                'lomin':lomin,
                'lomax':lomax,
                'ncomp':ncomp}
    metadata = os.path.join(direc,'download_info.txt') 

    # prepare station info (existing station list vs. fetching from client)
    if down_list:
        if not os.path.isfile(dlist):
            raise IOError('file %s not exist! double check!' % dlist)

        # read station info from list
        locs = pd.read_csv(dlist)               
        nsta = len(locs)
        chan = list(locs.iloc[:]['channel'])
        net  = list(locs.iloc[:]['network'])
        sta  = list(locs.iloc[:]['station'])
        lat  = list(locs.iloc[:]['latitude'])
        lon  = list(locs.iloc[:]['longitude'])

        # location info: useful for some occasion
        try:
            location = list(locs.iloc[:]['location'])
        except Exception as e:
            location = ['*']*nsta

    else:

        # calculate the total number of channels to download
        sta=[];net=[];chan=[];location=[];lon=[];lat=[];elev=[]
        nsta=0

        # loop through specified network, station and channel lists
        for inet in net_list:
            for ista in sta_list:
                for ichan in chan_list:
                    # gather station info
                    try:
                        inv = client.get_stations(network=inet,
                                                station=ista,
                                                channel=ichan,
                                                location='*',
                                                starttime=starttime,
                                                endtime=endtime,
                                                minlatitude=lamin,
                                                maxlatitude=lamax, 
                                                minlongitude=lomin, 
                                                maxlongitude=lomax,
                                                level='response')
                    except Exception as e:
                        print('Abort at L126 in S0A due to '+str(e))
                        sys.exit()

                    for K in inv:
                        for tsta in K:
                            sta.append(tsta.code)
                            net.append(K.code)
                            chan.append(ichan)
                            lon.append(tsta.longitude)
                            lat.append(tsta.latitude)
                            elev.append(tsta.elevation)
                            # sometimes one station has many locations and here we only get the first location
                            if tsta[0].location_code:
                                location.append(tsta[0].location_code)
                            else: location.append('*')
                            nsta+=1
        prepro_para['nsta'] = nsta

    # rough estimation on memory needs (assume float32 dtype)
    nsec_chunk = inc_hours/24*86400
    nseg_chunk = int(np.floor((nsec_chunk-cc_len)/step))+1
    npts_chunk = int(nseg_chunk*cc_len*samp_freq)
    memory_size = nsta*npts_chunk*4/1024**3
    if memory_size > MAX_MEM:
        raise ValueError('Require %5.3fG memory but only %5.3fG provided)! Reduce inc_hours to avoid this issue!' % (memory_size,MAX_MEM))


    ########################################################
    #################DOWNLOAD SECTION#######################
    ########################################################

    #--------MPI---------
    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    size = comm.Get_size()

    if rank==0:
        if not os.path.isdir(rootpath):
            os.mkdir(rootpath)
        if not os.path.isdir(direc):
            os.mkdir(direc)
        
        # output station list
        if not down_list:     
            dict = {'network':net,
                    'station':sta,
                    'channel':chan,
                    'latitude':lat,
                    'longitude':lon,
                    'elevation':elev}
            locs = pd.DataFrame(dict)        
            locs.to_csv(os.path.join(direc,'station.txt'),index=False)
        
        # save parameters for future reference
        fout = open(metadata,'w')
        fout.write(str(prepro_para));fout.close()

        # get MPI variables ready 
        all_chunk = noise_module.get_event_list(start_date[0],end_date[0],inc_hours)
        if len(all_chunk)<1:
            raise ValueError('Abort! no data chunk between %s and %s' % (start_date[0],end_date[0]))
        splits = len(all_chunk)-1
    else:
        splits,all_chunk = [None for _ in range(2)]

    # broadcast the variables
    splits = comm.bcast(splits,root=0)
    all_chunk  = comm.bcast(all_chunk,root=0)
    extra = splits % size

    tp = 0
    # MPI: loop through each time chunk 
    for ick in range(rank,splits,size):

        s1=obspy.UTCDateTime(all_chunk[ick])
        s2=obspy.UTCDateTime(all_chunk[ick+1]) 
        date_info = {'starttime':s1,'endtime':s2} 
        
        # keep a track of the channels already exists
        num_records = np.zeros(nsta,dtype=np.int16)

        # filename of the ASDF file
        ff=os.path.join(direc,all_chunk[ick]+'T'+all_chunk[ick+1]+'.h5')
        if not os.path.isfile(ff):
            with pyasdf.ASDFDataSet(ff,mpi=False,compression="gzip-3",mode='w') as ds:
                pass
        else:
            with pyasdf.ASDFDataSet(ff,mpi=False,mode='r') as rds:
                alist = rds.waveforms.list()
                for ista in range(nsta):
                    tname = net[ista]+'.'+sta[ista]
                    if tname in alist:
                        num_records[ista] = len(rds.waveforms[tname].get_waveform_tags())

        # appending when file exists
        with pyasdf.ASDFDataSet(ff,mpi=False,compression="gzip-3",mode='a') as ds:

            # loop through each channel
            for ista in range(nsta):

                # continue when there are alreay data for sta A at day X
                if num_records[ista] == ncomp:
                    continue

                # get inventory for specific station
                try:
                    sta_inv = client.get_stations(network=net[ista],
                                                station=sta[ista],
                                                location=location[ista],
                                                starttime=s1,
                                                endtime=s2,
                                                level="response")
                except Exception as e:
                    print(e);continue

                # add the inventory for all components + all time of this tation         
                try:
                    ds.add_stationxml(sta_inv) 
                except Exception: 
                    pass   

                try:
                    # get data
                    t0=time.time()
                    tr = client.get_waveforms(network=net[ista],
                                            station=sta[ista],
                                            channel=chan[ista],
                                            location=location[ista],
                                            starttime=s1,
                                            endtime=s2)
                    t1=time.time()
                except Exception as e:
                    print(e,'for',sta[ista]);continue
                    
                # preprocess to clean data  
                print(sta[ista])
                tr = noise_module.preprocess_raw(tr,sta_inv,prepro_para,date_info)
                t2 = time.time()
                tp += t2-t1

                if len(tr):
                    if location[ista] == '*':
                        tlocation = str('00')
                    else:
                        tlocation = location[ista]
                    new_tags = '{0:s}_{1:s}'.format(chan[ista].lower(),tlocation.lower())
                    ds.add_waveforms(tr,tag=new_tags)

                #if flag:
                print(ds,new_tags);print('downloading data %6.2f s; pre-process %6.2f s' % ((t1-t0),(t2-t1)))

    tt1=time.time()
    print('downloading step takes %6.2f s with %6.2f for preprocess' %(tt1-tt0, tp))

    comm.barrier()
    if rank == 0:
        sys.exit()

# Keeping this for backward compatibility, but the preferred entry point is: noisepy.py download
if __name__ == "__main__":
    download(default_rootpath, default_chan_list, default_sta_list, default_start_date, default_end_date, default_inc_hours)