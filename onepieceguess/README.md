# OnePieceGuess

Timed One Piece character guessing game for Red v3.5+. The bot fetches a character image from Fandom and posts a **blurred** version as an embed. Users guess with `.guess <name>`; fuzzy matching + aliases help with typos.

## Key Features
- Configurable interval, channel, reward
- Blurred image (gaussian or pixelate) with configurable strength
- Optional text hint (intro extract)
- Manage character pool and aliases via commands
- Optional reward payout via BeriCore

## Commands
[p]opguess — status  
[p]opguess toggle [on/off]  
[p]opguess setchannel <#channel>  
[p]opguess setinterval <seconds>  
[p]opguess setreward <amount>  
[p]opguess hint [on/off] [max_chars]  
[p]opguess blur                 # show blur settings  
[p]opguess blur mode <gaussian|pixelate>  
[p]opguess blur strength <1-64>  
[p]opguess char                 # list summary  
[p]opguess char add <title>  
[p]opguess char remove <title>  
[p]opguess char aliases <title> <a,b,c>  
[p]opguess import (attach JSON)  
[p]opguess export  
[p]opguess forcepost  
[p]opguess end  
[p]guess <name>                 # player guess

### Import format
Either:
- `["Monkey D. Luffy", "Marshall D. Teach", ...]`
- `{"characters":[...], "aliases": {"Marshall D. Teach": ["blackbeard","black beard","teach"]}}`
