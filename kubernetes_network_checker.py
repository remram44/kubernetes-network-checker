import argparse
import asyncio
import itertools
import kubernetes_asyncio.client as k8s_client
import kubernetes_asyncio.config as k8s_config
import kubernetes_asyncio.stream as k8s_stream
import logging
import prometheus_client
from tabulate import tabulate
import time


logger = logging.getLogger(__name__)


PROM_NETWORK_ISSUES = prometheus_client.Gauge(
    'netcheck_issues',
    "Number of detected network connectivity issues",
)
PROM_TESTED_NODES = prometheus_client.Gauge(
    'netcheck_tested_nodes',
    "Number of nodes tested",
)
PROM_TOTAL_NODES = prometheus_client.Gauge(
    'netcheck_total_nodes',
    "Number of nodes in cluster",
)


async def apply_async(func, iterable, *, max_tasks):
    iterable = iter(iterable)

    # Start N tasks
    tasks = {
        asyncio.ensure_future(func(*elem))
        for elem in itertools.islice(iterable, max_tasks)
    }

    while tasks:
        # Wait for any task to complete
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Poll them
        for task in done:
            tasks.discard(task)
            task.result()

        # Schedule new tasks
        for elem in itertools.islice(iterable, max_tasks - len(tasks)):
            tasks.add(asyncio.ensure_future(func(*elem)))


def generate_test_pairs(nodes):
    for node1 in nodes:
        for node2 in nodes:
            if node1 == node2:
                continue
            yield node1, node2


async def do_check(api, *, image, namespace):
    v1 = k8s_client.CoreV1Api(api)

    # List nodes
    nodes = await v1.list_node()
    node_names = sorted([node.metadata.name for node in nodes.items])
    logger.info("Discovered %d nodes", len(node_names))
    PROM_TOTAL_NODES.set(len(node_names))

    # Start pods on all nodes
    for node in node_names:
        name = 'netcheck-%s' % node
        pod = k8s_client.V1Pod(
            metadata=k8s_client.V1ObjectMeta(
                name=name,
                labels={
                    'app': 'netcheck',
                    'component': 'test',
                    'run': name,
                },
            ),
            spec=k8s_client.V1PodSpec(
                containers=[
                    k8s_client.V1Container(
                        name='web',
                        image=image,
                    ),
                ],
                node_name=node,
                restart_policy='Always',
            ),
        )
        await v1.create_namespaced_pod(
            namespace=namespace,
            body=pod,
        )
    logger.info("Created pods")

    # Start services for all pods
    for node in node_names:
        name = 'netcheck-%s' % node
        svc = k8s_client.V1Service(
            metadata=k8s_client.V1ObjectMeta(
                name=name,
                labels={
                    'app': 'netcheck',
                    'component': 'test',
                    'run': name,
                },
            ),
            spec=k8s_client.V1ServiceSpec(
                selector={
                    'app': 'netcheck',
                    'component': 'test',
                    'run': name,
                },
                ports=[
                    k8s_client.V1ServicePort(
                        name='web',
                        protocol='TCP',
                        port=80,
                    ),
                ],
            ),
        )
        await v1.create_namespaced_service(
            namespace=namespace,
            body=svc,
        )
    logger.info("Created services")

    start = time.time()
    starting = set()
    ready = set()
    failed = set()
    while time.time() < start + 120:
        await asyncio.sleep(5)

        pods = await v1.list_namespaced_pod(
            namespace=namespace,
            label_selector='app=netcheck,component=test',
        )
        starting = set()
        ready = set()
        failed = set()
        for pod in pods.items:
            if pod.status.phase == 'Pending':
                starting.add(pod.metadata.name)
            elif pod.status.phase == 'Running':
                ready.add(pod.metadata.name)
            else:
                failed.add(pod.metadata.name)

        if starting:
            logger.info("Waiting for pods to start...")
        else:
            break

    if failed:
        logger.error(
            "Some pods have failed: %s",
            ", ".join(sorted(failed)),
        )
    if starting:
        logger.error(
            "Some pods are still starting after 120s: %s",
            ", ".join(sorted(starting))
        )
    if not ready:
        logger.error("No pods are ready")
        raise
    logger.info("%d pods started", len(ready))
    ready_nodes = [name[len('netcheck-'):] for name in ready]

    # The test
    async with k8s_stream.WsApiClient() as api_ws:
        v1_ws = k8s_client.CoreV1Api(api_ws)

        reachability_matrix = {}

        async def check_pair(from_node, to_node):
            logger.info("Testing %s -> %s", from_node, to_node)
            resp = await v1_ws.connect_get_namespaced_pod_exec(
                name='netcheck-%s' % from_node,
                namespace=namespace,
                command=[
                    'curl',
                    '-s', '-o', '/dev/null',
                    '-w', 'netcheck_status=%{http_code}',
                    '--connect-timeout', '10',
                    'http://netcheck-%s/' % to_node,
                ],
                stderr=True, stdin=False, stdout=True, tty=False,
            )
            if resp == 'netcheck_status=200':
                reachability_matrix[(from_node, to_node)] = 'ok'
            else:
                reachability_matrix[(from_node, to_node)] = 'FAIL'

        # Run the test, a few at a time
        targets = generate_test_pairs(ready_nodes)
        await apply_async(check_pair, targets, max_tasks=10)

    # Update metric
    issues = 0
    for value in reachability_matrix.values():
        if value != 'ok':
            issues += 1
    PROM_NETWORK_ISSUES.set(issues)
    PROM_TESTED_NODES.set(len(ready_nodes))

    # Print report
    table = []
    for from_node in node_names:
        row = [from_node]
        for to_node in node_names:
            if from_node == to_node:
                row.append('')
            else:
                try:
                    status = reachability_matrix[(from_node, to_node)]
                except KeyError:
                    status = 'NOT RUN'
                row.append(status)
        table.append(row)
    logger.info(
        "Test complete:\n%s",
        tabulate(table, headers=[''] + node_names, tablefmt='simple'),
    )


async def cleanup(api, *, namespace):
    v1 = k8s_client.CoreV1Api(api)

    pods = await v1.list_namespaced_pod(
        namespace=namespace,
        label_selector='app=netcheck,component=test',
    )
    logger.info("Deleting %d pods...", len(pods.items))
    for pod in pods.items:
        await v1.delete_namespaced_pod(
            namespace=namespace,
            name=pod.metadata.name,
        )

    svcs = await v1.list_namespaced_service(
        namespace=namespace,
        label_selector='app=netcheck,component=test',
    )
    logger.info("Deleting %d services...", len(svcs.items))
    for svc in svcs.items:
        await v1.delete_namespaced_service(
            namespace=namespace,
            name=svc.metadata.name,
        )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        'kubernetes-network-checker',
        description=(
            "Checks network connectivity between pods and services on all "
            + "hosts"
        ),
    )
    parser.add_argument(
        '--once',
        help="Check once and exit",
        action='store_true',
    )
    parser.add_argument('--image', nargs=1, default=['nginx'])
    parser.add_argument('--namespace', nargs=1, default=['default'])
    parser.add_argument('--config', nargs=1)
    parser.add_argument('--metrics-port', nargs=1, default=['8080'])
    args = parser.parse_args()

    if not args.once:
        metrics_port = int(args.metrics_port[0], 10)
        logger.info("Serving metrics on port %d", metrics_port)
        prometheus_client.start_http_server(metrics_port)

    asyncio.run(amain(
        once=args.once,
        image=args.image[0],
        namespace=args.namespace[0],
        config=args.config[0] if args.config else None,
    ))


async def amain(*, once=False, image, namespace, config=None):
    if config is None:
        logger.info("Using in-cluster config")
    else:
        logger.info("Using a specified config file")

    while True:
        if config is None:
            k8s_config.load_incluster_config()
        else:
            await k8s_config.load_kube_config(config)

        logger.info("Running check, connecting to cluster...")
        async with k8s_client.ApiClient() as api:
            try:
                await do_check(api, image=image, namespace=namespace)
            finally:
                await cleanup(api, namespace=namespace)

        if once:
            break

        await asyncio.sleep(15 * 60)
