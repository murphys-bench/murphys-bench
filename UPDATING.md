# Updating Murphy's Bench

This page explains what the in-app buttons can and cannot do, and why. Read it
once. It will save you a confusing afternoon.

## The short version

Murphy's Bench runs as an ordinary, unprivileged user on your server. It cannot
install system-level things. That is deliberate, and it has a consequence you
need to know about:

**The Update button updates application code only.** If a release also adds a
system-level piece (a systemd unit, a sudoers rule, an OS package, an nginx
change), that part does not arrive. You have to run one command.

## Why it works that way

If Murphy's Bench could install system software by itself, it would need
administrative rights on your server. Anything that compromised the app, a web
vulnerability, a bad dependency, a stolen login, would then have those rights
too. So the app runs with the narrowest privileges that let it do its job, and
it cannot install a service or edit system configuration.

That is the right security decision. The cost is that the app cannot fully
upgrade itself, and until now it did not tell you when that mattered.

## What each button actually does

| Button | What it does | What it cannot do |
|---|---|---|
| **Back up now** | Runs a full backup and ships it to your configured destination. | Nothing. It works. |
| **Restore** | Lists backups on your destination and restores the one you pick. | Nothing, *if* its systemd units are installed. See below. |
| **Update** | Pulls new application code, runs migrations, restarts. | Cannot install systemd units, sudoers rules, OS packages or nginx config. |

### A note on Restore

Restore needs two systemd units to exist on your server. A fresh install puts
them there and the installer's verifier fails if they are missing, so a new
installation is fine.

If you installed Murphy's Bench *before* those units existed, pressing Update
brings you the restore screen but not the units behind it, and the page will
wait forever for something that is not running. Run the command below and it
works.

## When a release needs a command

The changelog says so, and from v0.11.0 onward the Update button checks for
itself and tells you. When that happens:

```bash
cd /opt/murphys-bench && git pull && scripts/install_units.sh
```

Order matters. Update first, because the unit definitions arrive with the code.
Running `install_units.sh` before updating installs the old set.

You will be asked for your password. That is the point: installing a system
service is an administrative act, and it should involve a human who has
administrative rights.

## Command-line recovery, always

Whatever state the app is in, the command line works:

```bash
scripts/restore.sh <backup-file>     # restore from a backup
scripts/verify_install.sh            # check this install is correct
```

`restore.sh` is the same code the Restore button runs. The button is a
convenience over it, never a replacement, and it is deliberately the thing that
keeps working when the app does not.

## Where this is going

Needing a command after certain updates is a real limitation, not a preference.
The current fix is honesty: the Update button now tells you when a release needs
one, instead of reporting success while having done half the job.

The permanent fix is to distribute Murphy's Bench as a signed system package.
The package installs a small, fixed, root-owned upgrade mechanism that the app
can *ask* to run but cannot alter. That gives you a button that does what it
says, without ever handing the web application administrative rights.

That work is underway. It changes how Murphy's Bench is installed and where its
data lives, so it will come with its own migration instructions and its own
warning. Until it lands, this page describes reality.
