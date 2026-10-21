/*
 * gtm-launch -- start a tenant run's CLI as that workspace's own OS user, and supervise it (MM9 second layer).
 *
 * The one privileged piece of the tenant boundary, so it is deliberately small and refuses by default. It holds
 * CAP_SETUID, CAP_SETGID and CAP_KILL (file capabilities, nothing else) and is executable only by the backend's
 * user, never by a run it has started. It does three jobs:
 *
 *  1. DROP. Takes the target identity from GTM_RUN_UID / GTM_RUN_GID (refusing anything outside the pool
 *     [UID_MIN, UID_MAX], so it can never become root or the backend's user), takes the program from GTM_REAL_CLI
 *     (refusing anything whose real path is not CLI_SUFFIX under CLI_PREFIX, or not root-owned and closed to
 *     group/other writes), closes every inherited descriptor above 2, drops supplementary groups, gid and uid
 *     (real, effective and saved), drops every capability, sets no_new_privs and umask 077, proves it cannot get
 *     back to root, and execs the program.
 *
 *  2. HAND OFF. A process's command line is readable by every user in the container (/proc/<pid>/cmdline), and the
 *     SDK puts the MCP configuration (every provider key the run holds) and the tenant's system prompt on it. So
 *     those flags are accepted ONLY with a path to a file the backend wrote under ARGS_PREFIX (0600, its own user's,
 *     read before the drop). The contents reach the program through an anonymous in-memory file, shown on its command
 *     line as /dev/fd/N, which only the program's own user can reopen. The inline forms of those flags, and the
 *     `--flag=value` spelling, are REFUSED: nothing secret can reach a command line through this program.
 *
 *  3. SUPERVISE. Once the program runs as another user the backend cannot signal it. So this process stays as the
 *     backend's user, keeps only CAP_KILL, forwards SIGTERM/SIGINT/SIGHUP/SIGQUIT to the program's whole process
 *     group, mirrors its exit status, and kills what the group left behind. The program gets SIGKILL if this process
 *     dies, so a hard kill of the supervisor cannot leave the CLI running. (What the CLI started in its OWN group
 *     is not reached by that: it ends when its stdin does. The denylist above covers the flags the SDK emits
 *     today; a new secret-bearing SDK flag would need adding to it.)
 *
 *  4. PROVISION (`gtm-launch --provision <tree>`, a separate mode with its own capabilities). A run can only write a
 *     tree its own user owns, and the backend no longer owns that tree. So this hands one workspace tree to the identity
 *     in GTM_RUN_UID / GTM_RUN_GID (the same pool check as a launch) and grants the backend's user rwx through POSIX
 *     ACLs: an access ACL on every entry and a default ACL on every directory, both naming the backend and the
 *     identity and nobody else. A default ACL replaces the umask for whatever is created below it, so what either side
 *     writes later stays reachable by both (a group would not: the run's umask 077 strips group bits, and the backend
 *     cannot be in a group per workspace). Accepted only from the backend's own uid, and only for a path that is
 *     already canonical and is exactly WORKSPACES_PREFIX plus one workspace id (never the root, the launch directory,
 *     anything below a tree, or anything a link resolves to). It then OPENS that path one component at a time from
 *     "/", never following a link, so no ancestor can be swapped for one between the check and the walk, and refuses a
 *     workspaces root its caller does not own. The walk never follows a link, refuses a device node,
 *     a hard-linked file (its other name may be outside the tree) and a mount inside the tree, and gives the tree's
 *     own root to the identity LAST: a refusal part-way leaves the root unclaimed, so the run stays refused.
 *     Holds CAP_CHOWN and CAP_FOWNER only, and only in this mode; a launch drops both before it does anything.
 *
 * Exit 126 for every refusal, with a one-line reason on stderr that never echoes an environment value or an argument.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <endian.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <limits.h>
#include <linux/capability.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <sys/xattr.h>
#include <unistd.h>

static unsigned long rlim_nproc = 1024;
static unsigned long rlim_fsize = 536870912;
static unsigned long rlim_cpu = 1800;

#ifndef UID_MIN
#define UID_MIN 20000
#endif
#ifndef UID_MAX
#define UID_MAX 29999
#endif
#ifndef CLI_SUFFIX
#define CLI_SUFFIX "/_bundled/claude"
#endif
#ifndef CLI_PREFIX
#define CLI_PREFIX "/usr/local/lib/python3.13/site-packages/claude_agent_sdk/"
#endif
#ifndef ARGS_PREFIX
#define ARGS_PREFIX "/app/data/workspaces/.launch/"
#endif
#ifndef WORKSPACES_PREFIX
#define WORKSPACES_PREFIX "/app/data/workspaces/"
#endif
#ifndef BACKEND_UID
#define BACKEND_UID 10001
#endif
_Static_assert(BACKEND_UID < UID_MIN, "the backend's user must not be a pool identity");
#define MAX_HELD 8
#define MAX_DEPTH 64
#define MAX_ID 64
#define MAX_ARG_BYTES (1 << 20)

/* Flags whose value is secret-bearing and may only arrive as a file under ARGS_PREFIX. */
static const char *const FILE_FLAGS[] = {"--mcp-config", "--settings", "--system-prompt-file",
                                         "--append-system-prompt-file", NULL};
/* Flags that carry the same content inline: never accepted. */
static const char *const INLINE_FLAGS[] = {"--system-prompt", "--append-system-prompt", "--agents", NULL};

struct held {
    int idx;
    char *buf;
    size_t len;
};

static volatile pid_t child_pid;

static int refuse(const char *why) {
    fprintf(stderr, "gtm-launch: refused: %s\n", why);
    return 126;
}

static long pool_id(const char *name) {
    const char *v = getenv(name);
    if (!v || !*v)
        return -1;
    char *end = NULL;
    errno = 0;
    long n = strtol(v, &end, 10);
    if (errno || *end || n < UID_MIN || n > UID_MAX)
        return -1;
    return n;
}

static int in_list(const char *const *list, const char *arg) {
    for (; *list; list++)
        if (strcmp(arg, *list) == 0)
            return 1;
    return 0;
}

/* `--flag=value` spelling of any flag above */
static int equals_form(const char *arg) {
    for (const char *const *l = FILE_FLAGS; *l; l++)
        if (strncmp(arg, *l, strlen(*l)) == 0 && arg[strlen(*l)] == '=')
            return 1;
    for (const char *const *l = INLINE_FLAGS; *l; l++)
        if (strncmp(arg, *l, strlen(*l)) == 0 && arg[strlen(*l)] == '=')
            return 1;
    return 0;
}

/* Read a file the backend wrote under ARGS_PREFIX: its own user's, 0600, a regular file, bounded. */
static int slurp(const char *path, struct held *h) {
    char real[PATH_MAX];
    if (!realpath(path, real) || strncmp(real, ARGS_PREFIX, strlen(ARGS_PREFIX)) != 0)
        return -1;
    int fd = open(real, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0)
        return -1;
    struct stat st;
    /* Ownership needs no check of its own: a 0600 file this process could open IS its user's, because it holds no
     * capability that overrides file permissions (a negative control proved a separate owner test cannot fail). */
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || (st.st_mode & 077) != 0 || st.st_size > MAX_ARG_BYTES) {
        close(fd);
        return -1;
    }
    h->buf = malloc((size_t)st.st_size + 1);
    if (!h->buf) {
        close(fd);
        return -1;
    }
    size_t got = 0;
    while (got < (size_t)st.st_size) {
        ssize_t n = read(fd, h->buf + got, (size_t)st.st_size - got);
        if (n <= 0) {
            close(fd);
            return -1;
        }
        got += (size_t)n;
    }
    close(fd);
    unlink(real); /* the secret lives on disk only until the launcher has it */
    h->len = got;
    return 0;
}

static void wipe(struct held *held, int n) {
    for (int i = 0; i < n; i++)
        if (held[i].buf) {
            explicit_bzero(held[i].buf, held[i].len);
            free(held[i].buf);
            held[i].buf = NULL;
        }
}

static int set_caps(__u32 effective_and_permitted) {
    struct __user_cap_header_struct hdr = {_LINUX_CAPABILITY_VERSION_3, 0};
    struct __user_cap_data_struct data[2];
    memset(data, 0, sizeof(data));
    data[0].effective = data[0].permitted = effective_and_permitted;
    return (int)syscall(SYS_capset, &hdr, data);
}

/* Close every inherited descriptor above 2 (all of them, not a fixed range). A closed 0-2 needs no handling here:
 * glibc re-opens them on /dev/null for a file-capability (AT_SECURE) binary before main runs, so a descriptor made
 * later cannot land on a standard stream (a live test pins it; removing a manual re-open proved it redundant). */
static void scrub_fds(void) {
#ifdef SYS_close_range
    if (syscall(SYS_close_range, 3U, ~0U, 0) != 0)
#endif
    {
        long max = sysconf(_SC_OPEN_MAX);
        if (max < 1024 || max > 65536)
            max = 65536;
        for (int fd = 3; fd < max; fd++)
            close(fd);
    }
}

static void forward(int sig) {
    pid_t c = child_pid;
    if (c > 0)
        kill(-c, sig);
}

/* Runs in the child only; never returns on success. */
static void become(long uid, long gid, const char *real, char **argv, struct held *held, int nheld, pid_t parent) {
    setpgid(0, 0);
    /* Nothing but 0-2 may reach the program: a descriptor the backend left open would be a read it never granted. */
    scrub_fds();
    gid_t g = (gid_t)gid;
    if (setgroups(1, &g) != 0)
        _exit(refuse("setgroups failed"));
    if (setresgid(g, g, g) != 0)
        _exit(refuse("setresgid failed"));
    if (setresuid((uid_t)uid, (uid_t)uid, (uid_t)uid) != 0)
        _exit(refuse("setresuid failed"));
    /* Enforce kernel resource limits (R7): setrlimit applied to the tenant UID */
    struct rlimit rl_nproc = {rlim_nproc, rlim_nproc};
    if (setrlimit(RLIMIT_NPROC, &rl_nproc) != 0)
        _exit(refuse("setrlimit RLIMIT_NPROC failed"));
    struct rlimit rl_fsize = {rlim_fsize, rlim_fsize};
    if (setrlimit(RLIMIT_FSIZE, &rl_fsize) != 0)
        _exit(refuse("setrlimit RLIMIT_FSIZE failed"));
    struct rlimit rl_cpu = {rlim_cpu, rlim_cpu};
    if (setrlimit(RLIMIT_CPU, &rl_cpu) != 0)
        _exit(refuse("setrlimit RLIMIT_CPU failed"));
    /* A non-root to non-root uid change does NOT clear the file capabilities that let us make it: drop every set. */
    if (set_caps(0) != 0)
        _exit(refuse("capset failed"));
    if (prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) != 0)
        _exit(refuse("clearing ambient capabilities failed"));
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0)
        _exit(refuse("no_new_privs failed"));
    if (setuid(0) == 0)
        _exit(refuse("still able to become root"));
    umask(077); /* what the run writes to a shared directory is its own */
    for (int i = 0; i < nheld; i++) {
        int fd = (int)syscall(SYS_memfd_create, "gtm-arg", 0); /* no CLOEXEC: the program inherits it */
        if (fd < 0)
            _exit(refuse("memfd_create failed"));
        size_t put = 0;
        while (put < held[i].len) {
            ssize_t n = write(fd, held[i].buf + put, held[i].len - put);
            if (n <= 0)
                _exit(refuse("writing an argument file failed"));
            put += (size_t)n;
        }
        char *path = malloc(32);
        if (!path)
            _exit(refuse("out of memory"));
        snprintf(path, 32, "/dev/fd/%d", fd);
        argv[held[i].idx] = path;
    }
    wipe(held, nheld);
    /* Set AFTER the credential change (which clears it), and re-check the parent: no window where we are orphaned. */
    if (prctl(PR_SET_PDEATHSIG, SIGKILL, 0, 0, 0) != 0 || getppid() != parent)
        _exit(refuse("supervisor gone"));
    argv[0] = (char *)real;
    execv(real, argv);
    _exit(refuse("exec failed"));
}

/* ── provisioning ──────────────────────────────────────────────────────────────────────────────────────── */

static uid_t prov_ident;
static dev_t prov_dev;

/* One POSIX ACL in the kernel's xattr format: owner, the backend, the identity, no group, mask, no other. Named
 * entries are sorted by id (BACKEND_UID < UID_MIN, asserted above), as the kernel requires. */
static int put_acl(const char *proc, const char *key, unsigned perm) {
    struct {
        __le16 tag;
        __le16 perm;
        __le32 id;
    } __attribute__((packed)) e[6] = {
        {htole16(0x01), htole16(perm), htole32(~0U)},           /* ACL_USER_OBJ */
        {htole16(0x02), htole16(perm), htole32(BACKEND_UID)},   /* ACL_USER: the backend */
        {htole16(0x02), htole16(perm), htole32(prov_ident)},    /* ACL_USER: the identity */
        {htole16(0x04), htole16(0), htole32(~0U)},              /* ACL_GROUP_OBJ */
        {htole16(0x10), htole16(perm), htole32(~0U)},           /* ACL_MASK */
        {htole16(0x20), htole16(0), htole32(~0U)},              /* ACL_OTHER */
    };
    unsigned char buf[4 + sizeof(e)];
    __le32 version = htole32(2);
    memcpy(buf, &version, 4);
    memcpy(buf + 4, e, sizeof(e));
    return setxattr(proc, key, buf, sizeof(buf), 0);
}

/* Grant the ACLs on the inode `fd` (an O_PATH descriptor, so it is the inode that was checked, not a name). */
static int grant(int fd, const struct stat *st) {
    char proc[40];
    snprintf(proc, sizeof(proc), "/proc/self/fd/%d", fd);
    unsigned perm = S_ISDIR(st->st_mode) ? 7 : (6 | ((st->st_mode & S_IXUSR) ? 1 : 0));
    if (put_acl(proc, "system.posix_acl_access", perm) != 0)
        return -1;
    if (S_ISDIR(st->st_mode) && put_acl(proc, "system.posix_acl_default", 7) != 0)
        return -1;
    return 0;
}

static int claim(int fd) {
    return fchownat(fd, "", prov_ident, prov_ident, AT_EMPTY_PATH | AT_SYMLINK_NOFOLLOW);
}

/* Hand one entry (and, for a directory, everything below it) to the identity. A directory is granted first, so the
 * backend can read it whoever owned it before, walked, and claimed last. Returns NULL or the refusal's reason. */
static const char *settle(int fd, int depth) {
    struct stat st;
    if (fstat(fd, &st) != 0)
        return "stat failed";
    if (st.st_dev != prov_dev)
        return "the tree holds a mount";
    if (S_ISCHR(st.st_mode) || S_ISBLK(st.st_mode))
        return "the tree holds a device node";
    if (S_ISREG(st.st_mode) && st.st_nlink > 1)
        return "the tree holds a hard-linked file, whose other name may be outside it";
    if (!S_ISDIR(st.st_mode)) {
        if (claim(fd) != 0)
            return "chown failed";
        /* a link is never followed and carries no ACL; a fifo or socket keeps only its owner */
        return (S_ISREG(st.st_mode) && grant(fd, &st) != 0) ? "setting the ACL failed" : NULL;
    }
    if (depth > MAX_DEPTH)
        return "the tree is too deep";
    if (grant(fd, &st) != 0)
        return "setting the ACL failed";
    int rd = openat(fd, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    DIR *d = rd < 0 ? NULL : fdopendir(rd);
    if (!d) {
        if (rd >= 0)
            close(rd);
        return "reading a directory failed";
    }
    const char *why = NULL;
    struct dirent *ent;
    errno = 0;
    while (!why && (ent = readdir(d)) != NULL) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0)
            continue;
        int child = openat(dirfd(d), ent->d_name, O_PATH | O_NOFOLLOW | O_CLOEXEC);
        if (child < 0) {
            why = "opening an entry failed";
            break;
        }
        why = settle(child, depth + 1);
        close(child);
        errno = 0;
    }
    if (!why && errno != 0)
        why = "reading a directory failed";
    closedir(d);
    if (!why && claim(fd) != 0)
        why = "chown failed";
    return why;
}

/* Exactly WORKSPACES_PREFIX + one workspace id, already canonical: no link anywhere in it, no trailing slash. */
static int workspace_tree(const char *arg) {
    char real[PATH_MAX];
    size_t pl = strlen(WORKSPACES_PREFIX);
    if (!realpath(arg, real) || strcmp(real, arg) != 0 || strncmp(real, WORKSPACES_PREFIX, pl) != 0)
        return -1;
    size_t n = strlen(real + pl);
    if (n < 1 || n > MAX_ID)
        return -1;
    for (const char *c = real + pl; *c; c++)
        if (!((*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z') || (*c >= '0' && *c <= '9') || *c == '-' ||
              *c == '_'))
            return -1;
    /* the private-file directory is never a workspace, whatever the prefixes are compiled as */
    if (strncmp(ARGS_PREFIX, real, strlen(real)) == 0 && ARGS_PREFIX[strlen(real)] == '/')
        return -1;
    return 0;
}

/* Open WORKSPACES_PREFIX from "/" one component at a time, never following a link (O_NOFOLLOW guards only the last
 * component of a path, so opening the whole string would trust every ancestor's name), and require the backend's own
 * root. Then the tree is opened relative to that descriptor, never by its full name. */
static int open_root(void) {
    char buf[PATH_MAX];
    snprintf(buf, sizeof(buf), "%s", WORKSPACES_PREFIX);
    int fd = open("/", O_PATH | O_DIRECTORY | O_CLOEXEC);
    char *save = NULL;
    for (char *seg = strtok_r(buf, "/", &save); fd >= 0 && seg; seg = strtok_r(NULL, "/", &save)) {
        int next = openat(fd, seg, O_PATH | O_NOFOLLOW | O_DIRECTORY | O_CLOEXEC);
        close(fd);
        fd = next;
    }
    struct stat st;
    if (fd >= 0 && (fstat(fd, &st) != 0 || st.st_uid != BACKEND_UID)) {
        close(fd);
        return -1;
    }
    return fd;
}

static int provision(int argc, char **argv) {
    if (set_caps((1U << CAP_CHOWN) | (1U << CAP_FOWNER)) != 0 ||
        prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) != 0 || prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0)
        return refuse("could not narrow to the provisioning capabilities");
    if (getuid() != BACKEND_UID)
        return refuse("only the backend's user may provision a tree");
    if (argc != 3)
        return refuse("usage: --provision <workspace tree>");
    long uid = pool_id("GTM_RUN_UID");
    long gid = pool_id("GTM_RUN_GID");
    if (uid < 0 || gid < 0 || uid != gid)
        return refuse("GTM_RUN_UID/GTM_RUN_GID missing, unequal or outside the pool");
    if (workspace_tree(argv[2]) != 0)
        return refuse("not one canonical workspace tree under the workspaces root");
    int root = open_root();
    if (root < 0)
        return refuse("the workspaces root is not the backend's own directory, reached without a link");
    int fd = openat(root, argv[2] + strlen(WORKSPACES_PREFIX), O_PATH | O_NOFOLLOW | O_DIRECTORY | O_CLOEXEC);
    close(root);
    struct stat st;
    if (fd < 0 || fstat(fd, &st) != 0)
        return refuse("the workspace tree cannot be opened");
    prov_ident = (uid_t)uid;
    prov_dev = st.st_dev;
    const char *why = settle(fd, 0);
    close(fd);
    return why ? refuse(why) : 0;
}

static void supervise(pid_t pid, int *status_out) {
    /* The supervisor: still the backend's user, holding only the right to signal. */
    child_pid = pid;
    setpgid(pid, pid);
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = forward;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);
    sigaction(SIGQUIT, &sa, NULL);
    if (set_caps(1U << CAP_KILL) != 0 || prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) != 0 ||
        prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) {
        kill(-pid, SIGKILL);
        *status_out = refuse("supervisor could not drop its capabilities");
        return;
    }
    /* Wait WITHOUT reaping, so the zombie keeps the pid reserved while we kill the group: after a reap the pid can
     * be reused, and with CAP_KILL a signal to a reused group would reach another tenant's run. */
    siginfo_t si;
    int rc;
    do {
        rc = waitid(P_PID, (id_t)pid, &si, WEXITED | WNOWAIT);
    } while (rc < 0 && errno == EINTR);
    if (rc < 0) {
        kill(-pid, SIGKILL);
        *status_out = refuse("waiting for the run failed");
        return;
    }
    kill(-pid, SIGKILL); /* whatever the program left running in its group */
    child_pid = 0;       /* stop forwarding before the pid is released */
    int status = 0;
    while (waitpid(pid, &status, 0) < 0 && errno == EINTR)
        ;
    if (WIFEXITED(status))
        *status_out = WEXITSTATUS(status);
    else if (WIFSIGNALED(status))
        *status_out = 128 + WTERMSIG(status);
    else
        *status_out = 1;
}

int main(int argc, char **argv) {
    if (argc < 1 || !argv[0])
        return refuse("no argv"); /* argv[0] is overwritten below: without one it would eat the terminator */
    if (argc >= 2 && strcmp(argv[1], "--provision") == 0)
        return provision(argc, argv);
    /* A launch never needs the provisioning capabilities: give them up before anything else happens. */
    if (set_caps((1U << CAP_SETUID) | (1U << CAP_SETGID) | (1U << CAP_KILL)) != 0)
        return refuse("could not narrow to the launch capabilities");
    signal(SIGCHLD, SIG_DFL); /* an inherited SIG_IGN would make waitpid fail and a failed run look successful */
    long uid = pool_id("GTM_RUN_UID");
    long gid = pool_id("GTM_RUN_GID");
    if (uid < 0 || gid < 0)
        return refuse("GTM_RUN_UID/GTM_RUN_GID missing or outside the pool");

    const char *cli = getenv("GTM_REAL_CLI");
    char real[PATH_MAX];
    if (!cli || !realpath(cli, real))
        return refuse("GTM_REAL_CLI missing or not found");
    if (strncmp(real, CLI_PREFIX, strlen(CLI_PREFIX)) != 0)
        return refuse("GTM_REAL_CLI is not under the allowed prefix");
    size_t rl = strlen(real), sl = strlen(CLI_SUFFIX);
    if (rl < sl || strcmp(real + rl - sl, CLI_SUFFIX) != 0)
        return refuse("GTM_REAL_CLI is not the bundled CLI");
    struct stat st;
    if (stat(real, &st) != 0 || st.st_uid != 0 || (st.st_mode & 022) != 0)
        return refuse("the CLI is not root-owned and closed to group/other writes");

    /* Secret-bearing flags: only as a file the backend wrote; read now, while we are still the backend's user. */
    struct held held[MAX_HELD];
    int nheld = 0;
    memset(held, 0, sizeof(held));
    for (int i = 1; i < argc; i++) {
        if (equals_form(argv[i]) || in_list(INLINE_FLAGS, argv[i])) {
            wipe(held, nheld);
            return refuse("a secret-bearing flag was given inline");
        }
        if (in_list(FILE_FLAGS, argv[i])) {
            if (i + 1 >= argc || nheld >= MAX_HELD) {
                wipe(held, nheld);
                return refuse("a secret-bearing flag has no usable value");
            }
            if (i + 2 < argc && argv[i + 2][0] != '-') {
                wipe(held, nheld);
                return refuse("a secret-bearing flag is variadic: a second value would pass unchecked");
            }
            held[nheld].idx = i + 1;
            if (slurp(argv[i + 1], &held[nheld]) != 0) {
                wipe(held, nheld);
                return refuse("a secret-bearing flag does not name a private file under the launch directory");
            }
            nheld++;
            i++;
        }
    }

    /* Parse optional resource limit overrides (R7) */
    const char *v_nproc = getenv("GTM_RLIMIT_NPROC");
    if (v_nproc && *v_nproc) {
        long n = strtol(v_nproc, NULL, 10);
        if (n >= 64 && n <= 8192) rlim_nproc = (unsigned long)n;
    }
    const char *v_fsize = getenv("GTM_RLIMIT_FSIZE");
    if (v_fsize && *v_fsize) {
        long long n = strtoll(v_fsize, NULL, 10);
        if (n >= 16777216LL && n <= 10737418240LL) rlim_fsize = (unsigned long)n;
    }
    const char *v_cpu = getenv("GTM_RLIMIT_CPU");
    if (v_cpu && *v_cpu) {
        long n = strtoll(v_cpu, NULL, 10);
        if (n >= 60 && n <= 86400) rlim_cpu = (unsigned long)n;
    }

    /* Nothing the program inherits may name the launcher's own inputs or limits. */
    unsetenv("GTM_RUN_UID");
    unsetenv("GTM_RUN_GID");
    unsetenv("GTM_REAL_CLI");
    unsetenv("GTM_RLIMIT_NPROC");
    unsetenv("GTM_RLIMIT_FSIZE");
    unsetenv("GTM_RLIMIT_CPU");

    pid_t parent = getpid();
    pid_t pid = fork();
    if (pid < 0) {
        wipe(held, nheld);
        return refuse("fork failed");
    }
    if (pid == 0) {
        become(uid, gid, real, argv, held, nheld, parent);
        _exit(126);
    }

    wipe(held, nheld);
    int exit_status = 0;
    supervise(pid, &exit_status);
    return exit_status;
}
