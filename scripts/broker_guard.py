from scripts.env import load,ROOT
load(ROOT/'secrets/broker.env')
from services.broker.alpaca import Alpaca
api=Alpaca()
cfg=api.request('PATCH','/v2/account/configurations',json={'max_margin_multiplier':'1','no_shorting':True})
if str(cfg.get('max_margin_multiplier'))!='1' or not cfg.get('no_shorting'):
    raise RuntimeError('Paper account guard verification failed')
print('Paper guard verified: 1x margin; no shorting.')
