/*
 * Print the Aos service instance identity from environment variables on start.
 *
 * C port of instance-startup-delay-python/src_any/startup-delay-demo-service.py: it prints
 * the instance identity immediately on launch (so the logged start time can be
 * used to measure instance startup delay) and then keeps logging periodically.
 */

#define _DEFAULT_SOURCE         /* gettimeofday */
#define _POSIX_C_SOURCE 200809L /* localtime_r */

#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <sys/time.h>

#define LOG_INTERVAL 10

static const char *env_or(const char *name, const char *fallback)
{
    const char *value = getenv(name);
    return (value != NULL) ? value : fallback;
}

/* Format the current local time as "%b %d %H:%M:%S.%f" (microsecond precision),
 * matching the Python service's now() output. */
static void now_str(char *buf, size_t len)
{
    struct timeval tv;
    struct tm tm;
    char base[32];

    gettimeofday(&tv, NULL);
    localtime_r(&tv.tv_sec, &tm);
    strftime(base, sizeof(base), "%b %d %H:%M:%S", &tm);
    snprintf(buf, len, "%s.%06d", base, (int)(tv.tv_usec % 1000000));
}

int main(void)
{
    const char *item = env_or("AOS_ITEM_ID", "");
    const char *subject = env_or("AOS_SUBJECT_ID", "");
    const char *instance_index = env_or("AOS_INSTANCE_INDEX", "<not set>");
    const char *instance_id = env_or("AOS_INSTANCE_ID", "<not set>");

    char ident[512];
    char ts[64];
    unsigned long iteration = 0;

    snprintf(ident, sizeof(ident), "{service:0:%s:%s:%s}", item, subject, instance_index);

    now_str(ts, sizeof(ts));
    printf("Instance started with ident: %s instance id: %s time: %s\n", ident, instance_id, ts);
    fflush(stdout);

    for (;;)
    {
        iteration++;
        now_str(ts, sizeof(ts));
        printf("Instance %s is running (iteration %lu) %s\n",
               ident, iteration, ts);
        fflush(stdout);
        sleep(LOG_INTERVAL);
    }

    return 0;
}
