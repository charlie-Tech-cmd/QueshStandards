import { Room, RoomEvent, Track } from "https://cdn.jsdelivr.net/npm/livekit-client@2.9.9/dist/livekit-client.esm.mjs";

const configNode = document.getElementById("meeting-config");
const videos = document.getElementById("livekit-videos");
const status = document.getElementById("livekit-status");

if (configNode && videos && status) {
  const config = JSON.parse(configNode.textContent);
  const room = new Room({ adaptiveStream: true, dynacast: true });

  const renderTrack = (track, participant) => {
    const element = track.attach();
    element.dataset.participant = participant.sid;
    if (track.kind === Track.Kind.Video) {
      element.className = "participant-video";
      element.autoplay = true;
      element.playsInline = true;
      videos.querySelector(".livekit-placeholder")?.remove();
      videos.append(element);
    } else {
      element.autoplay = true;
      element.hidden = true;
      videos.append(element);
    }
  };

  const setParticipantAudioTrack = (identity, trackSid) => {
    const form = [...document.querySelectorAll("[data-mute-form]")]
      .find(item => item.dataset.identity === identity);
    if (!form) return;
    form.elements.track_sid.value = trackSid || "";
    form.querySelector("[data-mute-button]").disabled = !trackSid;
  };

  room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
    renderTrack(track, participant);
    if (track.kind === Track.Kind.Audio) {
      setParticipantAudioTrack(participant.identity, publication.trackSid);
    }
  });
  room.on(RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
    track.detach().forEach(element => element.remove());
    videos.querySelector(`[data-participant="${participant.sid}"]`)?.remove();
    if (track.kind === Track.Kind.Audio) setParticipantAudioTrack(participant.identity, "");
  });
  room.on(RoomEvent.Disconnected, () => { status.textContent = "Disconnected"; });
  room.on(RoomEvent.ParticipantDisconnected, participant => {
    videos.querySelectorAll(`[data-participant="${participant.sid}"]`).forEach(element => element.remove());
  });

  const setControl = (id, label, action) => {
    const button = document.getElementById(id);
    if (button) button.addEventListener("click", async () => {
      try {
        await action();
        button.querySelector("span").textContent = label();
      } catch (error) {
        status.textContent = "A device control could not be changed.";
      }
    });
  };

  setControl("toggle-mic", () => room.localParticipant.isMicrophoneEnabled ? "Mute" : "Unmute",
    () => room.localParticipant.setMicrophoneEnabled(!room.localParticipant.isMicrophoneEnabled));
  setControl("toggle-camera", () => room.localParticipant.isCameraEnabled ? "Camera off" : "Camera on",
    () => room.localParticipant.setCameraEnabled(!room.localParticipant.isCameraEnabled));
  setControl("toggle-share", () => room.localParticipant.isScreenShareEnabled ? "Stop sharing" : "Share screen",
    () => room.localParticipant.setScreenShareEnabled(!room.localParticipant.isScreenShareEnabled));

  document.querySelectorAll("[data-mute-form]").forEach(form => {
    const button = form.querySelector("[data-mute-button]");
    button.addEventListener("click", async () => {
      const mutedField = form.elements.muted;
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Mute request failed");
        mutedField.value = String(!result.muted);
        button.textContent = result.muted ? "Unmute microphone" : "Mute microphone";
      } catch (error) {
        status.textContent = "Could not change this participant’s microphone state.";
        console.error("NexaWork participant mute failed", error);
      }
    });
  });

  try {
    await room.connect(config.url, config.token);
    await room.localParticipant.setMicrophoneEnabled(true);
    const meetingType = document.body.dataset.meetingType;
    if (meetingType !== "audio") await room.localParticipant.setCameraEnabled(true);
    status.textContent = "Connected · media is end-to-end to the SFU";
    for (const publication of room.remoteParticipants.values()) {
      for (const trackPublication of publication.trackPublications.values()) {
        if (trackPublication.track) {
          renderTrack(trackPublication.track, publication);
          if (trackPublication.kind === Track.Kind.Audio) {
            setParticipantAudioTrack(publication.identity, trackPublication.trackSid);
          }
        }
      }
    }
  } catch (error) {
    status.textContent = "Could not connect to the meeting. Check your camera, microphone, and network.";
    console.error("NexaWork meeting connection failed", error);
  }
}
