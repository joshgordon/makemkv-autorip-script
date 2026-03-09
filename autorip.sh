#!/bin/bash

# Defining variables for later use
SOURCEDRIVE="$1"
SCRIPTROOT="$(dirname """$(realpath "$0")""")"
CACHE="$(awk '/^cache/{print $1}' "$SCRIPTROOT/settings.cfg" | cut -d '=' -f2)"
DEBUG="$(awk '/^debug/{print $1}' "$SCRIPTROOT/settings.cfg" | cut -d '=' -f2)"
MINLENGTH="$(awk '/^minlength/{print $1}' "$SCRIPTROOT/settings.cfg" | cut -d '=' -f2)"
OUTPUTDIR="$(awk '/^outputdir/' "$SCRIPTROOT/settings.cfg" | cut -d '=' -f2 | cut -f1 -d"#" | xargs)"
SLACKWEBHOOK="$(awk '/^slackwebhook/' "$SCRIPTROOT/settings.cfg" | cut -d '=' -f2 | cut -f1 -d"#" | xargs)"
WEBPORT="$(awk '/^webport/{print $1}' "$SCRIPTROOT/settings.cfg" | cut -d '=' -f2 | cut -f1 -d"#" | xargs)"
WEBPORT="${WEBPORT:-8080}"
ARGS=""

# Check if the source drive has actually been set and is available
if [ -z "$SOURCEDRIVE" ]; then
	echo "[ERROR] Source Drive is not defined."
	echo "        When calling this script manually, make sure to pass the drive path as a variable: ./autorip.sh [DRIVE]"
	exit 1
fi
setcd -i "$SOURCEDRIVE" | grep --quiet 'Disc found'
if [ ! $? ]; then
        echo "[ERROR] $SOURCEDRIVE: Source Drive is not available."
        exit 1
fi

# Construct the arguments for later use
if [[ $OUTPUTDIR == ""\~*"" ]]; then
	if [[ $OUTPUTDIR == ""\~/*"" ]]; then
		OUTPUTDIR=$(echo "$(eval echo ~"${SUDO_USER:-$USER}")/${OUTPUTDIR:2}" | sed 's:/*$::')
	else
		OUTPUTDIR="$(eval echo ~"${SUDO_USER:-$USER}")"
	fi
fi
if [ -d "$OUTPUTDIR" ]; then
	:
else
	echo "[ERROR]: The output directory specified in settings.conf is invalid!"
	exit 1
fi
if [ -d "$SCRIPTROOT/logs" ]; then
	:
else
	echo "[ERROR]: Log directory under $SCRIPTROOT/logs is missing! Trying to create it."
	mkdir "$SCRIPTROOT/logs"
	exit 1
fi

if [ -z "$CACHE" ]; then
	if [ "$CACHE" = "-1" ]; then
		:
	elif [[ "$CACHE" =~ ^[0-9]+$ ]]; then
		ARGS="--cache=$CACHE"
	fi
fi
if [ "$DEBUG" = "true" ]; then
	ARGS="$ARGS --debug"
fi
if [[ "$MINLENGTH" =~ ^[0-9]+$ ]]; then
	ARGS="$ARGS --minlength=$MINLENGTH"
else
	ARGS="$ARGS --minlength=0"
fi

# Match unix drive name to Make-MKV drive number and check it
SOURCEMMKVDRIVE=$(makemkvcon --robot --noscan --cache=1 info disc:9999 | grep "$SOURCEDRIVE" | grep -o -E '[0-9]+' | head -1)
if [ -z "$SOURCEMMKVDRIVE" ]; then
	echo "[ERROR] $SOURCEDRIVE: Make-MKV Source Drive is not defined."
	exit 1
fi

echo "[INFO] $SOURCEDRIVE: Started ripping process"

#Extract DVD Title from Drive

DISKTITLERAW=$(blkid -o value -s LABEL "$SOURCEDRIVE")
DISKTITLERAW=${DISKTITLERAW// /_}
NOWDATE=$(date +"%F_%H-%M-%S")
DISKTITLE="${DISKTITLERAW}_-_$NOWDATE"
STATUSFILE="${SCRIPTROOT}/logs/status_$(basename $SOURCEDRIVE).json"
PROGRESSFILE="${SCRIPTROOT}/logs/${NOWDATE}_${DISKTITLERAW}_progress.log"


if [ -n "$SLACKWEBHOOK" ]; then
	curl -s -o /dev/null -X POST -H 'Content-type: application/json' \
		--data "{\"text\":\"Starting rip of *${DISKTITLERAW}* on \`${SOURCEDRIVE}\`\"}" \
		"$SLACKWEBHOOK"
fi
python3 -c "
import json
with open('$STATUSFILE', 'w') as f:
    json.dump({
        'drive': '$SOURCEDRIVE',
        'title': '$DISKTITLERAW',
        'start_time': '$(date -Iseconds)',
        'status': 'ripping',
        'log_file': '${NOWDATE}_${DISKTITLERAW}.log',
        'progress_file': '${NOWDATE}_${DISKTITLERAW}_progress.log'
    }, f)
"
mkdir "$OUTPUTDIR/$DISKTITLE"
makemkvcon mkv --messages="${SCRIPTROOT}/logs/${NOWDATE}_$DISKTITLERAW.log" --progress="$PROGRESSFILE" --noscan --robot $ARGS disc:"$SOURCEMMKVDRIVE" all "${OUTPUTDIR}/${DISKTITLE}"
RIPRESULT=$?
STATUS="complete"
[ $RIPRESULT -gt 1 ] && STATUS="failed"
python3 -c "
import json
with open('$STATUSFILE') as f:
    d = json.load(f)
d.update({'status': '$STATUS', 'exit_code': $RIPRESULT, 'end_time': '$(date -Iseconds)'})
with open('$STATUSFILE', 'w') as f:
    json.dump(d, f)
"
if [ $RIPRESULT -le 1 ]; then
	echo "[INFO] $SOURCEDRIVE: Ripping finished (exit code $RIPRESULT), ejecting"
	SLACK_MSG="Ripping finished for *${DISKTITLERAW}* on \`${SOURCEDRIVE}\` (exit code ${RIPRESULT})"
else
	echo "[ERROR] $SOURCEDRIVE: RIPPING FAILED (exit code $RIPRESULT), ejecting. Please check the logs under ${SCRIPTROOT}/logs/${NOWDATE}_${DISKTITLERAW}.log"
	SLACK_MSG=":x: Ripping FAILED for *${DISKTITLERAW}* on \`${SOURCEDRIVE}\` (exit code ${RIPRESULT}). Check logs: \`${SCRIPTROOT}/logs/${NOWDATE}_${DISKTITLERAW}.log\`"
fi
if [ -n "$SLACKWEBHOOK" ]; then
	curl -s -o /dev/null -X POST -H 'Content-type: application/json' \
		--data "{\"text\":\"${SLACK_MSG}\"}" \
		"$SLACKWEBHOOK"
	if [ $RIPRESULT -gt 1 ] && [ -f "${SCRIPTROOT}/logs/${NOWDATE}_${DISKTITLERAW}.log" ]; then
		SLACK_LOG_PAYLOAD=$(python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    content = ''.join(f.readlines()[-50:])
print(json.dumps({'text': '\`\`\`' + content + '\`\`\`'}))
" "${SCRIPTROOT}/logs/${NOWDATE}_${DISKTITLERAW}.log")
		curl -s -o /dev/null -X POST -H 'Content-type: application/json' \
			--data "$SLACK_LOG_PAYLOAD" \
			"$SLACKWEBHOOK"
	fi
fi
eject "$SOURCEDRIVE"
