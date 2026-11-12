# Security

Friday is designed for a single LAN. The backend authenticates devices with a
local CA over `wss://`, stores only a hash of each device identity, and gates
first enrollment behind an explicit pairing window. It is not hardened for
exposure to the internet, and the setup network password compiled into the
firmware is a provisioning default, not a secret.

If you find a problem in the pairing, transport, token handling or tool
confirmation paths, email todhruvg@gmail.com rather than opening a public
issue. Include the commit you tested and how to reproduce it. There is no
bounty; you will get a reply and credit in the fix if you want it.
