package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"
)

const memoriaBase = "http://localhost:19998"

type MemoriaClient struct {
	client *http.Client
}

type AgentInfo struct {
	ID              string   `json:"id"`
	Project         string   `json:"project"`
	Task            string   `json:"task"`
	Status          string   `json:"status"`
	Files           []string `json:"files"`
	CommitLog       []string `json:"commit_log"`
	StartedAt       float64  `json:"started_at"`
	LastHeartbeat   float64  `json:"last_heartbeat"`
	ConflictsWarned []string `json:"conflicts_warned"`
}

type TaskInfo struct {
	ID          string  `json:"id"`
	Project     string  `json:"project"`
	Title       string  `json:"title"`
	Status      string  `json:"status"`
	AssignedTo  string  `json:"assigned_to"`
	Result      string  `json:"result"`
	Error       string  `json:"error"`
	CreatedAt   float64 `json:"created_at"`
}

type MemoryEntry struct {
	Project string   `json:"project"`
	Entries []string `json:"entries"`
	Count   int      `json:"count"`
}

type RecallResult struct {
	Query   string         `json:"query"`
	Count   int            `json:"count"`
	Results []RecallHit    `json:"results"`
}

type RecallHit struct {
	ID      string `json:"id"`
	Title   string `json:"title"`
	Summary string `json:"summary"`
	Source  string `json:"source"`
	Room    string `json:"room,omitempty"`
	From    string `json:"from,omitempty"`
}

type MemoriaConfig struct {
	MemoryLimit                 int    `json:"memory_limit"`
	PollInterval                int    `json:"poll_interval"`
	AgentStaleSec               int    `json:"agent_stale_sec"`
	ChitchatPollInterval        int    `json:"chitchat_poll_interval"`
	ChitchatConsolidateThreshold int   `json:"chitchat_consolidate_threshold"`
	ChitchatMaxMessages         int    `json:"chitchat_max_messages"`
	SleepCycleHours             int    `json:"sleep_cycle_hours"`
	SessionMaxRecords           int    `json:"session_max_records"`
	AutoAcceptThreshold         int    `json:"auto_accept_threshold"`
	ChitchatURL                 string `json:"chitchat_url"`
	Port                        int    `json:"port"`
	Host                        string `json:"host"`
}

type HealthInfo struct {
	Ok              bool     `json:"ok"`
	SessionsIndexed int      `json:"sessions_indexed"`
	Topics          []string `json:"topics"`
	MemoriaVersion  string   `json:"memoria_version"`
	DBExists        bool     `json:"db_exists"`
}

func NewMemoriaClient() *MemoriaClient {
	return &MemoriaClient{
		client: &http.Client{Timeout: 10 * time.Second},
	}
}

func (m *MemoriaClient) get(path string, dest interface{}) error {
	resp, err := m.client.Get(memoriaBase + path)
	if err != nil {
		return fmt.Errorf("GET %s: %w", path, err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read %s: %w", path, err)
	}
	if err := json.Unmarshal(body, dest); err != nil {
		return fmt.Errorf("decode %s: %w", path, err)
	}
	return nil
}

func (m *MemoriaClient) Health() (*HealthInfo, error) {
	var h HealthInfo
	if err := m.get("/health", &h); err != nil {
		return nil, err
	}
	return &h, nil
}

func (m *MemoriaClient) GetConfig() (*MemoriaConfig, error) {
	var cfg MemoriaConfig
	if err := m.get("/config", &cfg); err != nil {
		return nil, err
	}
	return &cfg, nil
}

func (m *MemoriaClient) Agents() ([]AgentInfo, error) {
	var result struct {
		Agents []AgentInfo `json:"agents"`
		Count  int         `json:"count"`
	}
	if err := m.get("/agents", &result); err != nil {
		return nil, err
	}
	return result.Agents, nil
}

func (m *MemoriaClient) Tasks(project string) ([]TaskInfo, error) {
	path := "/tasks"
	if project != "" {
		path += "?project=" + project
	}
	var result struct {
		Tasks []TaskInfo `json:"tasks"`
		Count int        `json:"count"`
	}
	if err := m.get(path, &result); err != nil {
		return nil, err
	}
	return result.Tasks, nil
}

func (m *MemoriaClient) Memory(project string) (*MemoryEntry, error) {
	var result MemoryEntry
	if err := m.get("/memory/"+project, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

func (m *MemoriaClient) TriggerConsolidation() error {
	resp, err := m.client.Post(memoriaBase+"/chitchat/consolidate", "application/json", nil)
	if err != nil {
		return fmt.Errorf("consolidate: %w", err)
	}
	defer resp.Body.Close()
	return nil
}

func (m *MemoriaClient) ClearProposals() error {
	req, err := http.NewRequest("DELETE", memoriaBase+"/proposals?confirm=true", nil)
	if err != nil {
		return fmt.Errorf("clear proposals req: %w", err)
	}
	resp, err := m.client.Do(req)
	if err != nil {
		return fmt.Errorf("clear proposals: %w", err)
	}
	defer resp.Body.Close()
	return nil
}

func (m *MemoriaClient) Recall(query string, limit int) ([]RecallHit, error) {
	path := fmt.Sprintf("/recall?q=%s&limit=%d", url.QueryEscape(query), limit)
	var result RecallResult
	if err := m.get(path, &result); err != nil {
		return nil, err
	}
	return result.Results, nil
}
