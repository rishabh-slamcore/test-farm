Post discussions on 
Architecture of test-farm v0
it was decided to reduce the scope of the test-farm in light of practical considerations of using LXD containers to mimic aware devices. The more prudent route was to use real devices to side-step the effort needed for creating LXD containers.
To that effect the test-farm would instead provide utility named disruptor to impair the network according to scenario file provided. 

Context:

Devices are connected only to office wifi

Device running the disruptor sits in the middle. The network routes have to be created such a way that traffic from the updater can reach the devices only through the disruptor , even while the Updater itself might be connected to office wifi.

Updater in this case could be complete hub instance or hawkbit server instance. 
which runs on another device separate from the disruptor. Devices are actual aware devices in office. 
Disruptor device will have two physical NICs. On one NIC, all devices will be reachable.(This will be the office wifi)
On another NIC, we will have the updater also reachable. This will be a private network with only disruptor and updater having IPs on it. The updater will be connected to office wifi on another nic, so it could reach devices via office wifi but we will have to stop that by adding rules to office router. 

Questions:

What are the differences between disruptor and test-farm?
test-farm utility checked the validity of bundle downloaded by providing a POST endpoint for reporting status of download. disruptor only job is to impair the network while its running. The responsibility for checking what defines successful download now falls to the updater .

What would running a test look like now?
 In process of running a test, one would have to start the disruptor with a scenario. Network impairments will apply while the disruptor is running. User will then have to kick-off “download” from the updater themselves. Once validation is performed as defined by the updater , one would have to stop the disruptor manually. 

Can we disrupt a single client?
To be further investigated but initial work suggest this should be possible using tc to apply impairments on single devices but much more complex tc configuration. Also discovery of devices available to the disruptor will be done via mDNS.

Which all devices will get affected?
Since the disruptor uses mDNS to discover available devices, all aware devices on same subnet as the disruptor . A simple solution to limit this would be hardcoding a list of device names and providing this list to the disruptor. Also since impairments are only applied on the disruptor's client facing interface, the device should only see traffic routed through the disruptor affected.

How do prevent the updates from reaching the devices over wifi interface rather than through disruptor?
This will be important to verify but We’d need to add a route to the office network to reach the isolated subnet via the disruptor. We also need a separate route on the isolated subnet to route traffic for the main office subnet through the disruptor. But actual enforcement comes down to:
Listening to traffic on the updater from isolated interface only.
Partially blocking outgoing traffic from the updater with firewall rules on the updater itself.