import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import EmojiPicker from "emoji-picker-react";

import {
  Ban,
  Flag,
  MessageCircle,
  MoreHorizontal,
  RefreshCw,
  SkipForward,
  UserPlus,
  UserRound,
  Video,
  Check,
  X,
  Bell,
} from "lucide-react";

const API = import.meta.env.VITE_API_URL || "https://affinityplus-api.onrender.com";
const WS_BASE = API.replace(/^http/, "ws");

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}

type Gender = "male" | "female" | "non_binary" | "prefer_not_to_say";
type SessionGender = "any" | "male" | "female";

type Person = {
  user_id: number;
  username: string;
  score: number;
  explanation: string;
  bio: string;
  looking_for: string;
  interests: string[];
  age?: number | null;
  gender?: string;
  country?: string;
  state?: string;
  city?: string;
  district?: string;
  vibe?: string;
  shared_interests?: string[];
  is_online?: boolean;
  match_tier?: string;
};

type Stage = "home" | "finding" | "person" | "call";

type SessionResponse = {
  token: string;
  user: { id: number; username: string; account_type: "registered" | "guest" };
};

type ChatMessage = {
  id: string;
  mine: boolean;
  text: string;
};

type LocationData = {
  city: string;
  district: string;
  state: string;
  country: string;
};

type CallRoomProps = {
  token: string;
  currentUserId: number;
  person: Person;
  connectionId: number;
  onEnd: () => void;
  onNext: () => void;
  onAddFriend: () => void;
  onReport: () => void;
  onBlock: () => void;
  friendAdded: boolean;
  friendRequestState?: "none" | "pending" | "accepted";
};

const INDIA_STATES = ["Auto-detect", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry"];

function ageBracket(age?: number | null) {
  if (!age) return "18+";
  if (age <= 24) return "18–24";
  if (age <= 34) return "25–34";
  if (age <= 44) return "35–44";
  if (age <= 59) return "45–59";
  return "60+";
}

function genderLabel(gender?: string) {
  return gender === "female" ? "Woman" : gender === "male" ? "Man" : gender === "non_binary" ? "Non-binary" : "Prefer not to say";
}

function locationLabel(loc: LocationData) {
  return [loc.district || loc.city, loc.state, loc.country].filter(Boolean).join(", ");
}

function shortLocationLabel(loc: LocationData) {
  return [loc.district || loc.city, loc.state].filter(Boolean).join(", ");
}

async function reverseGeocode(lat: number, lon: number): Promise<LocationData> {
  // BigDataCloud's browser reverse-geocode endpoint does not require a key.
  const r = await fetch(
    `https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${encodeURIComponent(lat)}&longitude=${encodeURIComponent(lon)}&localityLanguage=en`,
  );
  if (!r.ok) throw new Error("Reverse geocoding failed");
  const d = await r.json();
  const administrative = Array.isArray(d.localityInfo?.administrative) ? d.localityInfo.administrative : [];
  const districtEntry = administrative.find((item: any) => /district|county/i.test(String(item.description || "")))
    || administrative.find((item: any) => Number(item.adminLevel) === 6);
  const district = String(d.suburb || d.neighbourhood || d.district || districtEntry?.name || "");
  return {
    city: String(d.city || d.locality || ""),
    district,
    state: String(d.principalSubdivision || ""),
    country: String(d.countryName || ""),
  };
}

async function ipFallback(): Promise<LocationData> {
  const r = await fetch("https://ipapi.co/json/");
  if (!r.ok) throw new Error("IP location failed");
  const d = await r.json();
  return {
    city: String(d.city || ""),
    district: String(d.district || d.city || ""),
    state: String(d.region || ""),
    country: String(d.country_name || ""),
  };
}

async function detectLocation(): Promise<LocationData> {
  if ("geolocation" in navigator) {
    try {
      const pos = await new Promise<GeolocationPosition>((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: false,
          timeout: 9000,
          maximumAge: 10 * 60 * 1000,
        });
      });
      const result = await reverseGeocode(pos.coords.latitude, pos.coords.longitude);
      if (result.city || result.country) return result;
    } catch {
      // Permission denied/network failure -> IP fallback.
    }
  }
  const fallback = await ipFallback();
  if (fallback.city || fallback.country) return fallback;
  throw new Error("Automatic location could not be determined.");
}

function CallRoom({
  token,
  currentUserId,
  person,
  connectionId,
  onEnd,
  onNext,
  onAddFriend,
  onReport,
  onBlock,
  friendAdded,
  friendRequestState = "none",
}: CallRoomProps) {
  const localVideo = useRef<HTMLVideoElement>(null);
  const remoteVideo = useRef<HTMLVideoElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const pendingCandidates = useRef<RTCIceCandidateInit[]>([]);
  const chatMessagesRef = useRef<HTMLDivElement>(null);
  const [callState, setCallState] = useState("Starting camera and microphone…");
  const [micOn, setMicOn] = useState(true);
  const [cameraOn, setCameraOn] = useState(true);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatText, setChatText] = useState("");
  const [error, setError] = useState("");
  const [remoteOnline, setRemoteOnline] = useState(false);
  const [emojiOpen, setEmojiOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);

  useEffect(() => {
    const el = chatMessagesRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  useEffect(() => {
    let disposed = false;

    async function start() {
      try {
        if (!navigator.mediaDevices?.getUserMedia) {
          throw new Error("Camera and microphone are not available in this browser.");
        }

        const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
        if (disposed) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }

        localStreamRef.current = stream;
        if (localVideo.current) localVideo.current.srcObject = stream;

        const pc = new RTCPeerConnection({
          iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
        });
        pcRef.current = pc;
        stream.getTracks().forEach((track) => pc.addTrack(track, stream));

        pc.ontrack = (event) => {
          const [remoteStream] = event.streams;
          if (remoteVideo.current && remoteStream) {
            remoteVideo.current.srcObject = remoteStream;
            setRemoteOnline(true);
            setCallState("Connected");
          }
        };

        pc.onicecandidate = (event) => {
          if (event.candidate && wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type: "ice-candidate", data: event.candidate.toJSON() }));
          }
        };

        pc.onconnectionstatechange = () => {
          const state = pc.connectionState;
          if (state === "connected") {
            setCallState("Connected");
            setRemoteOnline(true);
          } else if (state === "disconnected") {
            setCallState("Connection interrupted");
          } else if (state === "failed") {
            setCallState("Could not establish a direct call");
          }
        };

        const ws = new WebSocket(`${WS_BASE}/ws/connection/${connectionId}?token=${encodeURIComponent(token)}`);
        wsRef.current = ws;
        ws.onopen = () => setCallState("Waiting for your connection to join…");

        ws.onmessage = async (event) => {
          const msg = JSON.parse(event.data);
          const currentPc = pcRef.current;
          if (!currentPc) return;

          if (msg.type === "room_state") {
            if (msg.peers?.length) {
              setRemoteOnline(true);
              setCallState("Connecting…");
              const peerId = Number(msg.peer_id);
              if (currentUserId < peerId) {
                const offer = await currentPc.createOffer();
                await currentPc.setLocalDescription(offer);
                ws.send(JSON.stringify({ type: "offer", data: currentPc.localDescription }));
              }
            }
          } else if (msg.type === "peer_joined") {
            setRemoteOnline(true);
            setCallState("Connecting…");
            if (currentUserId < Number(msg.user_id)) {
              const offer = await currentPc.createOffer();
              await currentPc.setLocalDescription(offer);
              ws.send(JSON.stringify({ type: "offer", data: currentPc.localDescription }));
            }
          } else if (msg.type === "offer") {
            await currentPc.setRemoteDescription(msg.data);
            for (const candidate of pendingCandidates.current) {
              await currentPc.addIceCandidate(candidate).catch(() => {});
            }
            pendingCandidates.current = [];
            const answer = await currentPc.createAnswer();
            await currentPc.setLocalDescription(answer);
            ws.send(JSON.stringify({ type: "answer", data: currentPc.localDescription }));
          } else if (msg.type === "answer") {
            await currentPc.setRemoteDescription(msg.data);
            for (const candidate of pendingCandidates.current) {
              await currentPc.addIceCandidate(candidate).catch(() => {});
            }
            pendingCandidates.current = [];
          } else if (msg.type === "ice-candidate") {
            if (currentPc.remoteDescription) {
              await currentPc.addIceCandidate(msg.data).catch(() => {});
            } else {
              pendingCandidates.current.push(msg.data);
            }
          } else if (msg.type === "chat") {
            setMessages((old) => [
              ...old,
              { id: `${Date.now()}-${Math.random()}`, mine: Number(msg.from) === currentUserId, text: String(msg.text) },
            ]);
          } else if (msg.type === "peer_left") {
            setRemoteOnline(false);
            setCallState("They left the call");
            if (remoteVideo.current) remoteVideo.current.srcObject = null;
          } else if (msg.type === "hangup") {
            setRemoteOnline(false);
            setCallState("They ended the call");
          }
        };

        ws.onerror = () => setError("Real-time connection failed. Refresh and try again.");
        ws.onclose = () => {
          if (!disposed) setCallState("Call ended");
        };
      } catch (e) {
        if (!disposed) {
          setError((e as Error).message || "Camera/microphone permission is required.");
          setCallState("Waiting for camera and microphone permission");
        }
      }
    }

    start();

    return () => {
      disposed = true;
      try { wsRef.current?.send(JSON.stringify({ type: "hangup" })); } catch {}
      wsRef.current?.close();
      pcRef.current?.close();
      localStreamRef.current?.getTracks().forEach((track) => track.stop());
      if (localVideo.current) localVideo.current.srcObject = null;
      if (remoteVideo.current) remoteVideo.current.srcObject = null;
    };
  }, [connectionId, currentUserId, token]);

  function toggleMic() {
    const next = !micOn;
    localStreamRef.current?.getAudioTracks().forEach((track) => (track.enabled = next));
    setMicOn(next);
  }

  function toggleCamera() {
    const next = !cameraOn;
    localStreamRef.current?.getVideoTracks().forEach((track) => (track.enabled = next));
    setCameraOn(next);
  }

  function sendChat(e: FormEvent) {
    e.preventDefault();
    const text = chatText.trim();
    if (!text || wsRef.current?.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "chat", text }));
    setChatText("");
    setEmojiOpen(false);
  }

  function addEmoji(emoji: string) {
    setChatText((t) => t + emoji);
  }

  function endCall() {
    try { wsRef.current?.send(JSON.stringify({ type: "hangup" })); } catch {}
    onEnd();
  }

  return (
    <div className="call-stage">
      <div className="call-topbar">
        <div>
          <div className="eyebrow">LIVE CONNECTION</div>
          <h1>Talking with <span>{person.username}</span></h1>
          <div className={`presence ${remoteOnline ? "online" : ""}`}>
            <i /> {remoteOnline ? callState : "Waiting for them to join…"}
          </div>
        </div>

        <div className="call-topbar-actions">
          <button className="link-action" onClick={() => { try { wsRef.current?.send(JSON.stringify({ type: "hangup" })); } catch {} onNext(); }} title="Try someone else">Try someone else →</button>
          <div className="more-wrap">
            <button className="more-trigger" onClick={() => setMoreOpen((v) => !v)} aria-expanded={moreOpen} aria-haspopup="menu" title="More actions">
              <MoreHorizontal size={18} /> <span>More</span>
            </button>
            {moreOpen && (
              <div className="more-menu" role="menu">
                <button role="menuitem" onClick={() => { onAddFriend(); setMoreOpen(false); }}>
                  <UserPlus size={15} /> {friendAdded || friendRequestState === "accepted" ? "Friends" : friendRequestState === "pending" ? "Request sent" : "Add friend"}
                </button>
                <button role="menuitem" onClick={() => { onEnd(); setMoreOpen(false); }}>
                  <RefreshCw size={15} /> Reconnect later
                </button>
                <button role="menuitem" onClick={() => { onReport(); setMoreOpen(false); }}>
                  <Flag size={15} /> Report
                </button>
                <button className="danger-item" role="menuitem" onClick={() => { onBlock(); setMoreOpen(false); }}>
                  <Ban size={15} /> Block
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {error && <div className="call-error">{error}</div>}

      <div className="call-layout">
        <section className="video-panel">
          <div className="remote-video-wrap">
            <video ref={remoteVideo} className="remote-video" autoPlay playsInline />
            {!remoteOnline && (
              <div className="waiting-overlay">
                <div className="person-avatar">{person.username[0].toUpperCase()}</div>
                <strong>{person.username}</strong>
                <span>{callState}</span>
                <small>Open this same connection on their device to start the call.</small>
              </div>
            )}
            <div className="video-label">● {person.username}</div>
          </div>

          <div className="local-video-wrap">
            <video ref={localVideo} className="local-video" autoPlay muted playsInline />
            <div className="video-label">You</div>
          </div>

          <div className="call-controls">
            <button className={micOn ? "control" : "control off"} onClick={toggleMic}>
              {micOn ? "🎙" : "🔇"} <span>{micOn ? "Mute" : "Unmute"}</span>
            </button>
            <button className={cameraOn ? "control" : "control off"} onClick={toggleCamera}>
              {cameraOn ? "▣" : "□"} <span>{cameraOn ? "Camera" : "Camera off"}</span>
            </button>
            <button className="end-control" onClick={endCall}>☎ End</button>
          </div>
        </section>

        <aside className="chat-panel">
          <div className="chat-head">
            <strong><MessageCircle size={16} /> Chat</strong>
            <span>Private connection</span>
          </div>
          <div className="chat-messages" ref={chatMessagesRef}>
            {messages.length === 0 && (
              <div className="chat-empty">
                <span>💬</span>
                <strong>Say hello.</strong>
                <small>Your messages are relayed in real time while you're connected.</small>
              </div>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`bubble ${m.mine ? "mine" : ""}`}>{m.text}</div>
            ))}
          </div>
          {emojiOpen && (
            <div className="emoji-picker-native" role="dialog" aria-label="Emoji picker">
              <EmojiPicker
                onEmojiClick={(emojiData) => addEmoji(emojiData.emoji)}
                theme="dark"
                width="100%"
                height={360}
                lazyLoadEmojis
                previewConfig={{ showPreview: false }}
              />
            </div>
          )}
          <form className="chat-form" onSubmit={sendChat}>
            <button type="button" className="emoji-toggle" aria-label="Emoji" onClick={() => setEmojiOpen((v) => !v)}>🙂</button>
            <input value={chatText} onChange={(e) => setChatText(e.target.value)} placeholder={`Message ${person.username}…`} maxLength={1000} />
            <button aria-label="Send">↑</button>
          </form>
        </aside>
      </div>
    </div>
  );
}

export default function App() {
  const [token, setToken] = useState(localStorage.getItem("token") || "");
  const [accountType, setAccountType] = useState<"registered" | "guest">(
    (localStorage.getItem("account_type") as "registered" | "guest") || "registered",
  );
  const [currentUserId, setCurrentUserId] = useState<number | null>(null);
  const [stage, setStage] = useState<Stage>("home");
  const [person, setPerson] = useState<Person | null>(null);
  const [connectionId, setConnectionId] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [profileOpen, setProfileOpen] = useState(false);
  const [connectionsOpen, setConnectionsOpen] = useState(false);
  const [socialPanel, setSocialPanel] = useState<"friends" | "requests" | "history" | "blocked">("friends");
  const [connections, setConnections] = useState<any[]>([]);
  const [incomingRequests, setIncomingRequests] = useState<any[]>([]);
  const [outgoingRequests, setOutgoingRequests] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [blocked, setBlocked] = useState<any[]>([]);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportReason, setReportReason] = useState("Harassment or abusive behavior");
  const [reportDetails, setReportDetails] = useState("");
  const [onlineCount, setOnlineCount] = useState<number | null>(null);
  const [autoConnectIn, setAutoConnectIn] = useState<number | null>(null);
  const [friendAdded, setFriendAdded] = useState(false);
  const [friendRequestState, setFriendRequestState] = useState<"none" | "pending" | "accepted">("none");

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [signupAge, setSignupAge] = useState("");
  const [signupGender, setSignupGender] = useState<Gender>("prefer_not_to_say");

  const [bio, setBio] = useState("");
  const [lookingFor, setLookingFor] = useState("");
  const [interests, setInterests] = useState("");
  const [age, setAge] = useState("");
  const [gender, setGender] = useState<Gender>("prefer_not_to_say");
  const [country, setCountry] = useState("");
  const [stateName, setStateName] = useState("");
  const [city, setCity] = useState("");
  const [district, setDistrict] = useState("");
  const [vibe, setVibe] = useState("");

  const [sessionGender, setSessionGender] = useState<SessionGender>(
    (localStorage.getItem("affinity_session_gender") as SessionGender) || "any",
  );
  const [stateFilter, setStateFilter] = useState(localStorage.getItem("affinity_state_filter") || "Auto-detect");
  const [locationStatus, setLocationStatus] = useState("Location will be detected automatically.");
  const [manualLocation, setManualLocation] = useState(false);
  const [locationEditOpen, setLocationEditOpen] = useState(false);
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const [onboardingBusy, setOnboardingBusy] = useState(false);

  async function api(path: string, opt: RequestInit = {}) {
    const h = new Headers(opt.headers);
    h.set("Content-Type", "application/json");
    if (token) h.set("Authorization", `Bearer ${token}`);
    const r = await fetch(API + path, { ...opt, headers: h });
    const text = await r.text();
    let d: any = {};
    try { d = text ? JSON.parse(text) : {}; } catch { d = { detail: text }; }
    if (!r.ok) {
      const detail = Array.isArray(d.detail)
        ? d.detail.map((x: { msg: string }) => x.msg).join(", ")
        : d.detail;
      throw Error(detail || "Something went wrong");
    }
    return d;
  }

  function saveSession(d: SessionResponse) {
    localStorage.setItem("token", d.token);
    localStorage.setItem("account_type", d.user.account_type);
    setToken(d.token);
    setAccountType(d.user.account_type);
    setCurrentUserId(d.user.id);
    setMessage("");
  }

  function setSessionChoice(value: SessionGender) {
    setSessionGender(value);
    localStorage.setItem("affinity_session_gender", value);
  }

  function setStateFilterChoice(value: string) {
    setStateFilter(value);
    localStorage.setItem("affinity_state_filter", value);
  }

  function openSocial(panel: "friends" | "requests" | "history" | "blocked") {
    setSocialPanel(panel);
    setConnectionsOpen(true);
    refreshSocialData();
  }

  async function auth(e: FormEvent) {
    e.preventDefault();
    setMessage("");

    if (mode === "register") {
      const numericAge = Number(signupAge);
      if (!Number.isInteger(numericAge) || numericAge < 18 || numericAge > 100) {
        setMessage("You must be 18 or older to use Affinity Plus.");
        return;
      }
    }

    try {
      const path = mode === "login" ? "/auth/login" : "/auth/register";
      const body = mode === "login"
        ? { username: username.trim(), password }
        : { username: username.trim(), password, age: Number(signupAge), gender: signupGender };
      const d = await api(path, { method: "POST", body: JSON.stringify(body) });
      saveSession(d);
      setPassword("");
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function guest() {
    try { saveSession(await api("/auth/guest", { method: "POST" })); }
    catch (e) { setMessage((e as Error).message); }
  }

  async function detectAndSaveLocation() {
    setLocationStatus("Requesting your location…");
    try {
      const loc = await detectLocation();
      setCity(loc.city);
      setDistrict(loc.district);
      setStateName(loc.state);
      setCountry(loc.country);
      await api("/me/location", { method: "PUT", body: JSON.stringify(loc) });
      setManualLocation(false);
      setLocationStatus(`Detected: ${locationLabel(loc) || "location saved"}`);
      return loc;
    } catch {
      setManualLocation(true);
      setLocationStatus("Automatic location was unavailable. Manual override is available as a last resort.");
      return null;
    }
  }

  async function load() {
    try {
      const d = await api("/me");
      setCurrentUserId(d.id);
      setAccountType(d.account_type);
      localStorage.setItem("account_type", d.account_type);

      const p = d.profile || {};
      setBio(p.bio || "");
      setLookingFor(p.looking_for || "");
      setInterests((p.interests || []).join(", "));
      setAge(p.age ? String(p.age) : "");
      setGender(p.gender || "prefer_not_to_say");
      setCountry(p.country || "");
      setStateName(p.state || "");
      setCity(p.city || "");
      setDistrict(p.district || p.city || "");
      const allowedVibes = ["Chill", "Deep talks", "Playful", "Flirty", "Just here to vibe"];
      setVibe(allowedVibes.includes(p.vibe) ? p.vibe : "");

      const needsAge = !p.age;
      const needsLocation = !p.city || !p.country;
      setOnboardingOpen(needsAge || needsLocation);

      if (needsLocation) {
        await detectAndSaveLocation();
      } else {
        setLocationStatus(`Detected: ${[p.district || p.city, p.state, p.country].filter(Boolean).join(", ")}`);
      }
    } catch {
      logout();
    }
  }

  useEffect(() => {
    if (token) load();
  }, [token]);

  async function refreshSocialData() {
    try {
      const [f, r, h, b] = await Promise.all([
        api("/connections"),
        api("/friend-requests"),
        api("/history?limit=60"),
        api("/blocked"),
      ]);
      setConnections(f.connections || []);
      setIncomingRequests(r.incoming || []);
      setOutgoingRequests(r.outgoing || []);
      setHistory(h.history || []);
      setBlocked(b.blocked || []);
    } catch {}
  }

  useEffect(() => {
    if (token && currentUserId) refreshSocialData();
  }, [token, currentUserId, stage]);

  // Keep friend-request badges fresh while the user is online.
  useEffect(() => {
    if (!token || !currentUserId) return;
    const id = window.setInterval(() => { void refreshSocialData(); }, 15000);
    return () => window.clearInterval(id);
  }, [token, currentUserId]);

  useEffect(() => {
    if (!token) return;
    let active = true;
    async function beat() {
      try {
        const d = await api("/presence/heartbeat", { method: "POST" });
        if (active) setOnlineCount(d.online_count);
      } catch {}
    }
    beat();
    const id = setInterval(beat, 15000);
    return () => { active = false; clearInterval(id); };
  }, [token]);

  async function saveProfile(close = true, announce = true) {
    const body = {
      bio,
      looking_for: lookingFor,
      interests: interests.split(",").map((x) => x.trim()).filter(Boolean),
      age: age ? Number(age) : null,
      gender,
      country,
      state: stateName,
      city,
      district,
      vibe,
      preferred_gender: "any",
      preferred_country: "any",
      preferred_state: "any",
      min_age: 18,
      max_age: 100,
    };
    await api("/me/profile", { method: "PUT", body: JSON.stringify(body) });
    if (close) setProfileOpen(false);
    if (announce) setMessage("Profile updated.");
  }

  async function finishOnboarding(e?: FormEvent) {
    e?.preventDefault();
    setOnboardingBusy(true);
    setMessage("");

    try {
      const numericAge = Number(age);
      if (!Number.isInteger(numericAge) || numericAge < 18 || numericAge > 100) {
        throw new Error("Enter a valid age from 18 to 100.");
      }

      if (!city || !country) {
        const loc = await detectAndSaveLocation();
        if (!loc?.city || !loc.country) {
          throw new Error("We couldn't detect your location. Use the manual override below.");
        }
      }

      await saveProfile(false, false);
      setOnboardingOpen(false);
      setMessage("You're ready to discover people.");
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setOnboardingBusy(false);
    }
  }

  async function find() {
    if (!age) {
      setOnboardingOpen(true);
      setMessage("Complete your 18+ age first.");
      return;
    }

    setStage("finding");
    setPerson(null);
    setConnectionId(null);
    setMessage("");
    setFriendAdded(false);
    setFriendRequestState("none");
    setAutoConnectIn(null);

    try {
      // Save optional profile details, but session gender is sent separately
      // so "Who do you want to connect with today?" is always per-session.
      await saveProfile(false, false);
      await new Promise((r) => setTimeout(r, 250));
      const d = await api(`/discover/next?gender=${encodeURIComponent(sessionGender)}&state=${encodeURIComponent(stateFilter === "Auto-detect" ? "" : stateFilter)}`);
      if (!d.found) {
        setStage("home");
        setMessage(d.message);
        return;
      }
      setPerson(d.person);
      setStage("person");
    } catch (e) {
      setStage("home");
      setMessage((e as Error).message);
    }
  }

  async function next() {
    if (!person) return;
    setStage("finding");
    setMessage("");
    setFriendAdded(false);
    setFriendRequestState("none");
    setAutoConnectIn(null);

    try {
      await api(`/discover/${person.user_id}/skip`, { method: "POST" });
      const d = await api(`/discover/next?gender=${encodeURIComponent(sessionGender)}&state=${encodeURIComponent(stateFilter === "Auto-detect" ? "" : stateFilter)}`);
      if (!d.found) {
        setPerson(null);
        setStage("home");
        setMessage(d.message);
        return;
      }
      setPerson(d.person);
      setStage("person");
    } catch (e) {
      setStage("home");
      setMessage((e as Error).message);
    }
  }

  async function endCurrentConnection(id = connectionId) {
    if (!id) return;
    try { await api(`/connections/${id}/end`, { method: "POST" }); } catch {}
  }

  async function nextFromCall() {
    await endCurrentConnection();
    setStage("finding");
    setConnectionId(null);
    setFriendAdded(false);
    setMessage("");
    try {
      const d = await api(`/discover/next?gender=${encodeURIComponent(sessionGender)}&state=${encodeURIComponent(stateFilter === "Auto-detect" ? "" : stateFilter)}`);
      if (!d.found) {
        setPerson(null);
        setStage("home");
        setMessage(d.message);
        return;
      }
      setPerson(d.person);
      setStage("person");
    } catch (e) {
      setStage("home");
      setMessage((e as Error).message);
    }
  }

  async function connect() {
    if (!person) return;
    try {
      const d = await api("/connections", {
        method: "POST",
        body: JSON.stringify({ receiver_id: person.user_id }),
      });
      setConnectionId(d.connection_id);
      setFriendAdded(Boolean(d.is_friend));
      setFriendRequestState(Boolean(d.is_friend) ? "accepted" : "none");
      setStage("call");
    } catch (e) {
      const text = (e as Error).message || "";
      if (/already (talking|connected)|no longer online|another match/i.test(text)) {
        setMessage("That person was taken by another live connection. Finding someone else…");
        await next();
        return;
      }
      setMessage(text);
    }
  }

  async function addFriendFromCall() {
    if (!connectionId || friendAdded || friendRequestState === "pending") return;
    try {
      const d = await api(`/connections/${connectionId}/friend-request`, { method: "POST" });
      setFriendRequestState(d.status === "accepted" ? "accepted" : "pending");
      setMessage(d.message || "Friend request sent. They need to accept it.");
      await refreshSocialData();
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function addFriendFromHistory(h: any) {
    if (!h.connection_id) {
      setMessage("Friend request can only be sent from an active call or an existing call connection.");
      return;
    }
    try {
      const d = await api(`/connections/${h.connection_id}/friend-request`, { method: "POST" });
      await refreshSocialData();
      setMessage(d.message || "Friend request sent. They need to accept it.");
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function acceptRequest(requestId: number) {
    try {
      await api(`/friend-requests/${requestId}/accept`, { method: "POST" });
      await refreshSocialData();
      setMessage("Friend request accepted.");
    } catch (e) { setMessage((e as Error).message); }
  }

  async function rejectRequest(requestId: number) {
    try {
      await api(`/friend-requests/${requestId}/reject`, { method: "POST" });
      await refreshSocialData();
      setMessage("Friend request declined.");
    } catch (e) { setMessage((e as Error).message); }
  }

  async function cancelRequest(requestId: number) {
    try {
      await api(`/friend-requests/${requestId}`, { method: "DELETE" });
      await refreshSocialData();
      setMessage("Friend request cancelled.");
    } catch (e) { setMessage((e as Error).message); }
  }

  function openPersonFromRecord(record: any, asFriend = false) {
    setPerson({
      user_id: record.user_id,
      username: record.username,
      score: 100,
      explanation: record.explanation || "You've connected before.",
      bio: record.bio || "",
      looking_for: record.looking_for || "",
      interests: record.interests || [],
      age: record.age,
      gender: record.gender,
      country: record.country,
      state: record.state,
      city: record.city,
      district: record.district,
      vibe: record.vibe,
      is_online: record.is_online,
    });
    setConnectionId(record.connection_id || null);
    setFriendAdded(asFriend);
    setFriendRequestState(asFriend ? "accepted" : "none");
    setConnectionsOpen(false);
  }

  function joinConnection(c: any) {
    openPersonFromRecord(c, true);
    setStage("call");
  }

  async function reconnectTo(h: any) {
    if (h.is_blocked) {
      setMessage("This person is blocked. Unblock them first.");
      return;
    }
    if (!h.is_online) {
      if (!h.is_friend) {
        await addFriendFromHistory(h);
      } else {
        setMessage(`${h.username} is offline right now.`);
      }
      return;
    }

    try {
      const d = await api("/connections", {
        method: "POST",
        body: JSON.stringify({ receiver_id: h.user_id }),
      });
      setPerson({
        user_id: h.user_id,
        username: h.username,
        score: 100,
        explanation: "You've connected before.",
        bio: h.bio || "",
        looking_for: h.looking_for || "",
        interests: h.interests || [],
        age: h.age,
        gender: h.gender,
        country: h.country,
        state: h.state,
        city: h.city,
        district: h.district,
        vibe: h.vibe,
        is_online: true,
      });
      setConnectionId(d.connection_id);
      setConnectionsOpen(false);
      setFriendAdded(Boolean(d.is_friend));
      setFriendRequestState(Boolean(d.is_friend) ? "accepted" : "none");
      setStage("call");
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function blockPerson(targetId: number) {
    try {
      await api(`/users/${targetId}/block`, { method: "POST" });
      setReportOpen(false);
      setMessage("Person blocked. They won't appear in your matches.");
      if (person?.user_id === targetId) {
        setPerson(null);
        setConnectionId(null);
        setStage("home");
      }
      await refreshSocialData();
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function unblockPerson(targetId: number) {
    try {
      await api(`/users/${targetId}/block`, { method: "DELETE" });
      await refreshSocialData();
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function reportPerson() {
    if (!person) return;
    try {
      await api(`/users/${person.user_id}/report`, {
        method: "POST",
        body: JSON.stringify({ reason: reportReason, details: reportDetails }),
      });
      setReportOpen(false);
      setReportDetails("");
      setMessage("Report submitted. Thanks for helping keep Affinity Plus safe.");
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function removeFriend(connectionIdToRemove: number) {
    try {
      await api(`/connections/${connectionIdToRemove}`, { method: "DELETE" });
      await refreshSocialData();
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  function logout() {
    setToken("");
    setCurrentUserId(null);
    setPerson(null);
    setConnectionId(null);
    setStage("home");
    setProfileOpen(false);
    setConnectionsOpen(false);
    setOnboardingOpen(false);
    localStorage.removeItem("token");
    localStorage.removeItem("account_type");
  }

  useEffect(() => {
    if (stage !== "person" || !person) return;
    let seconds = 3;
    setAutoConnectIn(seconds);
    const id = window.setInterval(() => {
      seconds -= 1;
      if (seconds <= 0) {
        window.clearInterval(id);
        setAutoConnectIn(null);
        void connect();
      } else {
        setAutoConnectIn(seconds);
      }
    }, 1000);
    return () => window.clearInterval(id);
  }, [stage, person?.user_id]);

  const personLocation = person ? [person.district || person.city, person.state, person.country].filter(Boolean).join(", ") : "";
  const ownAgeBracket = ageBracket(age ? Number(age) : null);

  if (!token) {
    return (
      <main className="auth-shell">
        <section className="hero">
          <div className="hero-brand">Affinity <span>Plus</span></div>
          <div className="eyebrow">AI SOCIAL DISCOVERY · 18+</div>
          <h1>Meet someone<br /><span>you'll actually click with.</span></h1>
          <p>One person at a time. Match by age, gender preference, interests, vibe and location — then talk face-to-face.</p>
          <div className="auth-pills">
            <span>✦ AI matching</span>
            <span>◉ Live video + audio</span>
            <span>💬 Private chat</span>
          </div>
          <div className="orb" />
        </section>

        <form className="auth-card" onSubmit={auth}>
          <div className="eyebrow">AFFINITY PLUS</div>
          <h2>{mode === "login" ? "Welcome back" : "Create your account"}</h2>
          <p className="muted">{message || "Only the essentials to get started."}</p>

          <label>USERNAME</label>
          <input autoComplete="username" placeholder="your_username" value={username} onChange={(e) => setUsername(e.target.value)} />

          <label>PASSWORD</label>
          <input autoComplete={mode === "login" ? "current-password" : "new-password"} type="password" placeholder="8+ characters" value={password} onChange={(e) => setPassword(e.target.value)} />

          {mode === "register" && (
            <>
              <div className="onboarding-two-col">
                <div>
                  <label>AGE · 18+</label>
                  <input type="number" min="18" max="100" value={signupAge} onChange={(e) => setSignupAge(e.target.value)} placeholder="Your age" />
                </div>
                <div>
                  <label>GENDER</label>
                  <select value={signupGender} onChange={(e) => setSignupGender(e.target.value as Gender)}>
                    <option value="male">Man</option>
                    <option value="female">Woman</option>
                    <option value="non_binary">Non-binary</option>
                    <option value="prefer_not_to_say">Prefer not to say</option>
                  </select>
                </div>
              </div>
              <div className="detected-location-note">
                <span><UserRound size={15} /> Location</span>
                <strong>District + city detected automatically after sign-in</strong>
                <small>No location dropdowns. You can correct it later in Edit profile.</small>
              </div>
            </>
          )}

          <button className="primary-auth">{mode === "login" ? "LOGIN →" : "CREATE ACCOUNT →"}</button>
          <div className="auth-divider"><span>or</span></div>
          <button type="button" className="guest-button" onClick={guest}>👤 CONTINUE AS GUEST</button>
          <button type="button" className="ghost auth-switch" onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setMessage("");
            setPassword("");
          }}>
            {mode === "login" ? "Don't have an account? Create one →" : "Already have an account? Login →"}
          </button>
          <p className="guest-note">18+ only. Guest sessions are instant; profile details can be completed later.</p>
        </form>
      </main>
    );
  }

  return (
    <main className="connect-shell">
      <header>
        <div className="brand">Affinity <span>Plus</span></div>
        <div className="header-actions">
          {accountType === "guest" && <span className="guest-badge">👤 GUEST</span>}
          {onlineCount !== null && <span className="header-online"><span className="pulse-dot" /> {onlineCount} online</span>}
          <button className="ghost small" onClick={() => openSocial("friends")}>Friends {connections.length ? `(${connections.length})` : ""}</button>
          <button className="ghost small request-nav" onClick={() => openSocial("requests")}>Requests {incomingRequests.length ? `(${incomingRequests.length})` : ""}<Bell size={13} /></button>
          <button className="ghost small" onClick={() => openSocial("history")}>History</button>
          <button className="ghost small" onClick={() => openSocial("blocked")}>Blocked</button>
          <button className="ghost small" onClick={() => setProfileOpen((v) => !v)}>
            {profileOpen ? "Close profile" : "Edit profile"}
          </button>
          <button className="ghost small" onClick={logout}>Exit</button>
        </div>
      </header>

      {profileOpen && (
        <section className="profile-drawer">
          <div className="eyebrow">EDIT PROFILE</div>
          <h2>Make your profile yours.</h2>

          <label>AGE · 18+</label>
          <input type="number" min="18" max="100" value={age} onChange={(e) => setAge(e.target.value)} />
          <div className="age-bracket-note">Display group: <strong>{ownAgeBracket}</strong> · matching uses your real age with a rolling window.</div>

          <label>GENDER</label>
          <select value={gender} onChange={(e) => setGender(e.target.value as Gender)}>
            <option value="male">Man</option>
            <option value="female">Woman</option>
            <option value="non_binary">Non-binary</option>
            <option value="prefer_not_to_say">Prefer not to say</option>
          </select>

          <label>LOCATION</label>
          <div className="location-detected-card">
            <div>
              <span className="eyebrow">AUTO-DETECTED</span>
              <strong>{locationLabel({ city, district, state: stateName, country }) || "Location unavailable"}</strong>
              <small>{locationStatus}</small>
            </div>
            <button type="button" className="mini-action" onClick={() => setLocationEditOpen((v) => !v)}>
              {locationEditOpen ? "Done" : "Change"}
            </button>
          </div>

          {locationEditOpen && (
            <div className="location-override">
              <small>Manual override is available if automatic location is wrong or unavailable.</small>
              <input value={district} onChange={(e) => setDistrict(e.target.value)} placeholder="District / neighborhood" />
              <input value={city} onChange={(e) => setCity(e.target.value)} placeholder="City" />
              <input value={stateName} onChange={(e) => setStateName(e.target.value)} placeholder="State / region" />
              <input value={country} onChange={(e) => setCountry(e.target.value)} placeholder="Country" />
              <button type="button" className="ghost small" onClick={detectAndSaveLocation}>Detect again</button>
            </div>
          )}

          <label>YOUR VIBE <span className="optional">OPTIONAL</span></label>
          <div className="vibe-chips">
            {["Chill 🌙", "Deep talks 💭", "Playful 😄", "Flirty 😉", "Just here to vibe 🎧"].map((label) => {
              const value = label.replace(/ [^ ]+$/, "");
              return <button type="button" key={label} className={vibe === value ? "selected" : ""} onClick={() => setVibe(vibe === value ? "" : value)}>{label}</button>;
            })}
          </div>

          <label>ABOUT YOU <span className="optional">OPTIONAL</span></label>
          <textarea value={bio} onChange={(e) => setBio(e.target.value)} placeholder="I love travelling, gaming, music…" />

          <label>WHAT ARE YOU LOOKING FOR? <span className="optional">OPTIONAL</span></label>
          <textarea value={lookingFor} onChange={(e) => setLookingFor(e.target.value)} placeholder="Friends, travel buddies, people to talk to…" />

          <label>INTERESTS <span className="optional">OPTIONAL</span></label>
          <input value={interests} onChange={(e) => setInterests(e.target.value)} placeholder="gaming, travel, anime, guitar, food" />

          <button onClick={() => saveProfile(true)}>Save profile ✦</button>
        </section>
      )}

      {connectionsOpen && (
        <section className="connections-drawer">
          <div className="eyebrow">YOUR SOCIAL SPACE</div>
          <div className="connections-title-row">
            <h2>{socialPanel === "friends" ? "Friends" : socialPanel === "requests" ? "Friend requests" : socialPanel === "history" ? "History" : "Blocked"}</h2>
            <button className="drawer-close" onClick={() => setConnectionsOpen(false)}>×</button>
          </div>

          {socialPanel === "friends" && (
            connections.length === 0
              ? <div className="connections-empty">No friends yet. Start a call and tap <b>＋ Add friend</b> if you want to keep the connection.</div>
              : <div className="connection-list">
                {connections.map((c) => (
                  <div className="connection-item" key={c.connection_id}>
                    <span className="mini-avatar">{c.username[0].toUpperCase()}</span>
                    <span className="connection-copy">
                      <strong>{c.username}</strong>
                      <small>{[c.age && `${c.age}y`, genderLabel(c.gender), c.district || c.city || c.state || c.country, c.vibe].filter(Boolean).join(" · ")}</small>
                    </span>
                    <button className="mini-action" onClick={() => joinConnection(c)}><Video size={13} /> Open</button>
                    <button className="mini-action danger" onClick={() => blockPerson(c.user_id)}><Ban size={13} /> Block</button>
                    <button className="mini-action" onClick={() => {
                      openPersonFromRecord(c, true);
                      setReportOpen(true);
                    }}><Flag size={13} /> Report</button>
                    {c.connection_id ? <button className="mini-action" onClick={() => removeFriend(c.connection_id)}>Remove</button> : c.friend_request_id ? <button className="mini-action" onClick={async () => { await api(`/friendships/${c.friend_request_id}`, { method: "DELETE" }); await refreshSocialData(); }}>Remove</button> : null}
                  </div>
                ))}
              </div>
          )}

          {socialPanel === "requests" && (
            <div className="requests-space">
              <div className="request-section">
                <div className="request-section-title">Incoming <span>{incomingRequests.length}</span></div>
                {incomingRequests.length === 0 ? <div className="connections-empty">No new friend requests.</div> : <div className="connection-list">
                  {incomingRequests.map((r) => (
                    <div className="connection-item request-item" key={r.request_id}>
                      <span className="mini-avatar">{r.username[0].toUpperCase()}</span>
                      <span className="connection-copy"><strong>{r.username}</strong><small>{r.is_online ? "● Online" : "Offline"} · {r.district || r.city || r.state || r.country || "Affinity Plus"}</small></span>
                      <button className="mini-action accept" onClick={() => acceptRequest(r.request_id)}><Check size={13} /> Accept</button>
                      <button className="mini-action quiet-danger" onClick={() => rejectRequest(r.request_id)}><X size={13} /> Decline</button>
                    </div>
                  ))}
                </div>}
              </div>
              <div className="request-section">
                <div className="request-section-title">Sent <span>{outgoingRequests.length}</span></div>
                {outgoingRequests.length === 0 ? <div className="connections-empty">No pending requests sent.</div> : <div className="connection-list">
                  {outgoingRequests.map((r) => (
                    <div className="connection-item request-item" key={r.request_id}>
                      <span className="mini-avatar">{r.username[0].toUpperCase()}</span>
                      <span className="connection-copy"><strong>{r.username}</strong><small>{r.is_online ? "● Online" : "Offline"} · Waiting for acceptance</small></span>
                      <button className="mini-action" onClick={() => cancelRequest(r.request_id)}>Cancel</button>
                    </div>
                  ))}
                </div>}
              </div>
            </div>
          )}

          {socialPanel === "history" && (
            history.length === 0
              ? <div className="connections-empty">Only people you have actually called appear here. Skips and reports stay out of History.</div>
              : <div className="connection-list">
                {history.map((h) => (
                  <div className="connection-item history-item" key={h.id}>
                    <span className="mini-avatar">{h.username[0].toUpperCase()}</span>
                    <span className="connection-copy">
                      <strong>{h.username}</strong>
                      <small>{h.is_online ? "● Online" : "Offline"} · {new Date(h.created_at).toLocaleString()}</small>
                    </span>
                    {h.is_blocked
                      ? <span className="record-muted">Blocked</span>
                      : h.is_online
                        ? <button className="mini-action reconnect" onClick={() => reconnectTo(h)}><RefreshCw size={13} /> Reconnect</button>
                        : h.is_friend
                          ? <span className="record-muted">Offline</span>
                          : <button className="mini-action" onClick={() => addFriendFromHistory(h)}><UserPlus size={13} /> Add friend</button>}
                  </div>
                ))}
              </div>
          )}

          {socialPanel === "blocked" && (
            blocked.length === 0
              ? <div className="connections-empty">No blocked users.</div>
              : <div className="connection-list">
                {blocked.map((b) => (
                  <div className="connection-item" key={b.user_id}>
                    <span className="mini-avatar">{b.username[0].toUpperCase()}</span>
                    <span className="connection-copy"><strong>{b.username}</strong><small>Blocked</small></span>
                    <button className="mini-action" onClick={() => unblockPerson(b.user_id)}>Unblock</button>
                  </div>
                ))}
              </div>
          )}
        </section>
      )}

      <section className="experience">
        {stage === "home" && (
          <div className="home-stage">
            <div className="eyebrow">DISCOVER PEOPLE</div>
            <h1>Who will you<br /><span>click with?</span></h1>
            <p>Choose who you want to meet today. This choice is per session and can change every time you go live.</p>

            <div className="session-picker">
              <div className="session-picker-head">
                <span>WHO DO YOU WANT TO CONNECT WITH TODAY?</span>
                <small>Your choice is never buried in profile settings.</small>
              </div>
              <div className="session-options">
                <button className={sessionGender === "male" ? "selected" : ""} onClick={() => setSessionChoice("male")}>👨 <span>Men</span></button>
                <button className={sessionGender === "female" ? "selected" : ""} onClick={() => setSessionChoice("female")}>👩 <span>Women</span></button>
                <button className={sessionGender === "any" ? "selected" : ""} onClick={() => setSessionChoice("any")}>◎ <span>Anyone</span></button>
              </div>
            </div>

            <div className="location-summary">
              <div className="session-picker-head">
                <span>LOCATION</span>
                <small>Detected automatically. Matching starts nearby, then widens if needed.</small>
              </div>
              <strong>{[district || city, stateName, country].filter(Boolean).join(", ") || "Detecting location…"}</strong>
            </div>

            <div className="state-filter">
              <div className="session-picker-head">
                <span>OPTIONAL STATE FILTER · INDIA</span>
                <small>Use this only when you want to stay within a particular state.</small>
              </div>
              <select value={stateFilter} onChange={(e) => setStateFilterChoice(e.target.value)}>
                {INDIA_STATES.map((state) => <option key={state} value={state}>{state}</option>)}
              </select>
            </div>

            <div className="home-context">
              <span>AGE GROUP <b>{ownAgeBracket}</b></span>
              <span>LOCATION <b>{[district || city, stateName, country].filter(Boolean).join(", ") || "Auto-detecting…"}</b></span>
              <span>18+ <b>Rolling age match</b></span>
            </div>

            <button className="connect-button" onClick={find}><span>✦</span> FIND MY MATCH</button>
            {message && <div className="toast">{message}</div>}
            <div className="microcopy"><span>✦ interests</span><span>◉ vibe</span><span>◉ real age window</span><span>◉ online-first</span><span>💬 live chat</span></div>
          </div>
        )}

        {stage === "finding" && (
          <div className="finding-stage">
            <div className="search-orbit"><span /><span /><span /></div>
            <div className="eyebrow">AI MATCHING</div>
            <h2>Finding your person…</h2>
            <p>Checking your session choice, rolling age window, compatibility and live presence.</p>
            <div className="finding-lines"><span>INTERESTS</span><span>VIBE</span><span>AGE</span><span>GENDER</span><span>LOCATION</span><span>ONLINE</span></div>
          </div>
        )}

        {stage === "person" && person && (
          <div className="person-stage">
            <div className="match-label"><span className="live-dot" /> POSSIBLE CONNECTION</div>
            <div className="person-card">
              <div className="person-avatar">
                {person.username[0].toUpperCase()}
                {person.is_online && <span className="avatar-online-dot" title="Online now" />}
              </div>
              <h2>{person.username}</h2>
              <div className="potential">
                {person.score}% compatibility
                {person.is_online && <span className="online-pill">● Online now</span>}
              </div>
              <div className="profile-meta">
                {[
                  person.age && `${person.age} · ${ageBracket(person.age)}`,
                  person.gender && genderLabel(person.gender),
                  personLocation,
                  person.vibe,
                ].filter(Boolean).map((x) => <span key={x}>◉ {x}</span>)}
              </div>
              <p className="bio">{person.bio || "This person has not added a bio yet."}</p>
              <div className="chips">{person.interests.map((i) => <span key={i}>{i}</span>)}</div>
              <div className="match-reason">
                <span>{person.explanation}</span>
                {(person.shared_interests ?? []).length > 0 && <small>Shared: {(person.shared_interests ?? []).join(", ")}</small>}
                {person.match_tier && <small className="tier-note">Matched via {person.match_tier}.</small>}
              </div>
              <div className="looking">Looking for: {person.looking_for || "a good conversation"}</div>

              <div className="auto-connect-row">
                <div className="auto-connect-status">
                  <span className="auto-connect-ring">{autoConnectIn ?? 3}</span>
                  <span>Connecting in {autoConnectIn ?? 3}s…</span>
                </div>
                <button className="skip-button" onClick={next}>Not now</button>
              </div>
            </div>
          </div>
        )}

        {stage === "call" && person && connectionId && (
          <CallRoom
            token={token}
            currentUserId={currentUserId || 0}
            person={person}
            connectionId={connectionId}
            onEnd={() => { void endCurrentConnection(); setStage("home"); setConnectionId(null); }}
            onNext={nextFromCall}
            onAddFriend={addFriendFromCall}
            friendAdded={friendAdded}
            friendRequestState={friendRequestState}
            onReport={() => setReportOpen(true)}
            onBlock={() => blockPerson(person.user_id)}
          />
        )}
      </section>

      {onboardingOpen && (
        <div className="modal-backdrop onboarding-backdrop">
          <form className="onboarding-modal" onSubmit={finishOnboarding}>
            <div className="eyebrow">QUICK SETUP · 18+</div>
            <h2>Just the essentials.</h2>
            <p>We only need your age and gender to start matching. Your district and city are detected automatically.</p>

            <div className="onboarding-two-col">
              <div>
                <label>AGE · 18+</label>
                <input type="number" min="18" max="100" value={age} onChange={(e) => setAge(e.target.value)} placeholder="Your age" required />
              </div>
              <div>
                <label>GENDER</label>
                <select value={gender} onChange={(e) => setGender(e.target.value as Gender)}>
                  <option value="male">Man</option>
                  <option value="female">Woman</option>
                  <option value="non_binary">Non-binary</option>
                  <option value="prefer_not_to_say">Prefer not to say</option>
                </select>
              </div>
            </div>

            <div className="onboarding-location">
              <div className="location-icon"><UserRound size={18} /></div>
              <div>
                <strong>District</strong>
                <span>{shortLocationLabel({ city, district, state: stateName, country }) || "Detecting…"}</span>
                <small>{locationStatus}</small>
              </div>
              <button type="button" className="ghost small" onClick={detectAndSaveLocation}>Detect</button>
            </div>

            {manualLocation && (
              <div className="location-override onboarding-override">
                <small>Last resort: enter your location manually.</small>
                <input value={district} onChange={(e) => setDistrict(e.target.value)} placeholder="District / neighborhood" />
                <input value={city} onChange={(e) => setCity(e.target.value)} placeholder="City" />
                <input value={stateName} onChange={(e) => setStateName(e.target.value)} placeholder="State / region" />
                <input value={country} onChange={(e) => setCountry(e.target.value)} placeholder="Country" />
              </div>
            )}

            {message && <div className="toast">{message}</div>}
            <button className="primary-auth" disabled={onboardingBusy}>
              {onboardingBusy ? "SETTING UP…" : "CONTINUE TO AFFINITY PLUS →"}
            </button>
          </form>
        </div>
      )}

      {reportOpen && person && (
        <div className="modal-backdrop">
          <section className="report-modal">
            <div className="eyebrow">SAFETY REPORT</div>
            <h2>Report {person.username}</h2>
            <p>Choose the closest reason. You can block them separately if you no longer want to interact.</p>
            <select value={reportReason} onChange={(e) => setReportReason(e.target.value)}>
              <option>Harassment or abusive behavior</option>
              <option>Spam or scam</option>
              <option>Hate or hateful conduct</option>
              <option>Sexual or inappropriate content</option>
              <option>Impersonation</option>
              <option>Threats or dangerous behavior</option>
              <option>Other</option>
            </select>
            <textarea value={reportDetails} onChange={(e) => setReportDetails(e.target.value)} maxLength={2000} placeholder="Optional details…" />
            <div className="modal-actions">
              <button className="ghost" onClick={() => setReportOpen(false)}>Cancel</button>
              <button className="primary-auth" onClick={reportPerson}>Submit report</button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
