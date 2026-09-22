"""Explicit entity matching with conservative handling of ambiguous coin names."""
import re
AMBIGUOUS={"OP","ON","NEAR","LINK","INJ","CORE","ACE","ONE","COMP","MOVE","MAGIC","TIME",
           "GAS","ID","SAFE","HYPE","LAYER","SUPER","MASK","T","IP","W","PUMP","FORM"}
AMBIGUOUS_NAMES={"near","optimism","render","story","world","on","movement","pump","internet computer",
                 "core","compound","official trump"}
CONTEXT=re.compile(r"\b(crypto|coin|token|blockchain|protocol|defi|staking|cryptocurrency)\b",re.I)

def match_entities(text,aliases=None):
    found=set()
    if re.search(r"\b(bitcoin|btc)\b",text,re.I):found.add("BTC")
    if re.search(r"\b(ethereum|ether|eth)\b",text,re.I):found.add("ETH")
    for symbol,names in (aliases or {}).items():
        if re.search(r"(?<!\w)\$"+re.escape(symbol)+r"\b",text,re.I):
            found.add(symbol);continue
        if len(symbol)>=3 and symbol not in AMBIGUOUS and re.search(r"\b"+re.escape(symbol)+r"\b",text):
            found.add(symbol);continue
        for name in names:
            if not isinstance(name,str) or len(name)<3 or name.upper()==symbol:
                continue
            for match in re.finditer(r"\b"+re.escape(name)+r"\b",text,re.I):
                context=text[max(0,match.start()-60):match.end()+60]
                if name.lower() not in AMBIGUOUS_NAMES or CONTEXT.search(context):
                    found.add(symbol);break
    return sorted(found)
