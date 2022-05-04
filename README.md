Kubernetes Network Checker
==========================

This is a simple tool that will run a pod on each node of your cluster, then try to reach other pods via a service. It then prints a matrix which should immediately show any networking issue.

Example:

```
$ kubernetes-network-checker --once --config ~/.kube/config
2022-05-04 11:32:04,241 INFO kubernetes_network_checker: Discovered 6 nodes
2022-05-04 11:32:04,959 INFO kubernetes_network_checker: Created pods
2022-05-04 11:32:05,503 INFO kubernetes_network_checker: Created services
2022-05-04 11:32:10,743 INFO kubernetes_network_checker: 6 pods started
2022-05-04 11:32:25,067 INFO kubernetes_network_checker: Test complete:
          master1    master2    master3    worker4    worker5    worker6
--------  ---------  ---------  ---------  ---------  ---------  ---------
master1              ok         ok         ok         ok         ok
master2   ok                    ok         ok         ok         ok
master3   ok         ok                    ok         ok         ok
worker4   ok         ok         ok                    ok         ok
worker5   ok         ok         ok         ok                    ok
worker6   ok         ok         ok         ok         ok
2022-05-04 11:32:25,297 INFO kubernetes_network_checker: Deleting 6 pods...
2022-05-04 11:32:28,624 INFO kubernetes_network_checker: Deleting 6 services...
```

You can either run the tool on your machine (use `--config`) or run it inside the cluster as a pod (it will use in-cluster configuration, e.g. a service account).

You can either run it once (use `--once`) or let it run continuously, in which case it will run a check every 15 minutes. In this case it will also expose metrics (for Prometheus) which you can use for alerting.
