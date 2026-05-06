# Use a router container with explicit routing for network impairment

test-farm will place a Router Container between the Update Server network and the client network, apply `tc` impairment on egress toward clients, and use explicit routes rather than NAT between the two subnets. This keeps client IPs visible to the Update Server and avoids per-client veth tracking plus IFB ingress shaping, while accepting that v1 impairment is uniform across the client fleet rather than independently controlled per client.
